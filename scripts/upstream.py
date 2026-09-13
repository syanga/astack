#!/usr/bin/env python3
"""Pin upstream skill origins and compare updates without changing local skills."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

from manage import atomic_write

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "upstream/manifest.json"


@contextmanager
def exclusive_operation():
    lock = REPO / ".cache/upstream/operation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another upstream operation is running; retry after it finishes") from None
        yield


def git(*arguments, allowed=(0,)):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false", *map(str, arguments)],
        capture_output=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1"),
    )
    if result.returncode not in allowed:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) == "." or "\\" in value:
        raise ValueError("Expected a relative file or directory path: {}".format(value))
    if any(part.lower() == ".git" for part in path.parts):
        raise ValueError("Git metadata is not a source path")
    return str(path)


def import_id(value):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("Import IDs must use lowercase letters, digits, and single hyphens")
    return value


def fetch(repository, ref):
    # Public HTTPS sources and local fixture repositories; never remote command helpers.
    if not (repository.startswith("https://") or Path(repository).is_absolute()):
        raise ValueError("Repository must be an HTTPS URL or an absolute local path")
    if not ref or ref.startswith("-"):
        raise ValueError("Invalid upstream ref")
    key = hashlib.sha256(repository.encode()).hexdigest()[:20]
    cache = REPO / ".cache/upstream" / (key + ".git")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", cache)
    git("-C", cache, "fetch", "--no-tags", "--depth=1", repository, ref)
    commit = git("-C", cache, "rev-parse", "FETCH_HEAD^{commit}").decode().strip()
    return cache, commit


def write_snapshot_file(root, filename, data, mode):
    # Git permits names that the host filesystem may treat as the same path.
    # Check every component, including directories, before replacing any bytes.
    current = root
    for part in PurePosixPath(filename).parts:
        child = current / part
        if child.exists() and part not in os.listdir(current):
            raise ValueError("Upstream paths collide on this filesystem: {}".format(filename))
        current = child
    atomic_write(current, data, mode)


def snapshot(cache, commit, paths, output, require_paths=False):
    output.mkdir(parents=True, exist_ok=True)
    for source_path in paths:
        source_path = relative_path(source_path)
        tree = git("-C", cache, "ls-tree", "-r", "-z", "--full-tree", commit, "--", source_path)
        if require_paths and not tree:
            raise ValueError("Upstream path does not exist at {}: {}".format(commit, source_path))
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            path = relative_path(raw_path.decode())
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError("Unsupported upstream symlink or submodule: {}".format(path))
            data = git("-C", cache, "cat-file", "blob", object_id)
            write_snapshot_file(output, path, data, 0o755 if mode == "100755" else 0o644)


def archive_snapshot(root, target):
    # One binary artifact keeps upstream .gitignore/.gitattributes from affecting
    # astack's Git index, and keeps archived instruction files out of discovery.
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(root).as_posix())
                mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
                entry.external_attr = (stat.S_IFREG | mode) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes())


@contextmanager
def baseline(identifier, entry):
    path = REPO / "upstream/bases" / (import_id(identifier) + ".zip")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["baseline_digest"]:
        raise ValueError("Missing or modified baseline for {}; restore it from astack Git history".format(identifier))
    with tempfile.TemporaryDirectory(prefix="astack-base-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                filename = relative_path(item.filename)
                mode = item.external_attr >> 16
                if not stat.S_ISREG(mode) or stat.S_IMODE(mode) not in (0o644, 0o755):
                    raise ValueError("Unsupported snapshot entry: {}".format(filename))
                write_snapshot_file(root, filename, archive.read(item), stat.S_IMODE(mode))
        yield root


def compare(left, right, summary=False):
    flags = ["--stat"] if summary else []
    data = git("diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", *flags,
               "--", left, right, allowed=(0, 1)).decode(errors="replace")
    # Keep generated patches readable instead of exposing random temporary directory names.
    return data.replace(str(left), "BASE").replace(str(right), "CURRENT")


def track(args, manifest):
    identifier = import_id(args.id)
    if identifier in manifest["imports"]:
        raise ValueError("{} is already pinned; keep its original baseline".format(identifier))
    source = manifest["sources"][args.source]
    local = relative_path(args.local)
    if PurePosixPath(local).parts[0] not in ("skills", "instructions"):
        raise ValueError("Local customization must live under skills/ or instructions/")
    paths = list(dict.fromkeys(relative_path(value) for value in [args.path, *args.include]))
    cache, commit = fetch(source["repository"], args.ref or source["ref"])
    target = REPO / "upstream/bases" / (identifier + ".zip")
    if target.exists():
        raise ValueError("Baseline already exists: {}".format(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=target.parent) as temporary:
        staged = Path(temporary) / "base"
        snapshot(cache, commit, paths, staged, require_paths=True)
        packed = Path(temporary) / "base.zip"
        archive_snapshot(staged, packed)
        entry = {
            "source": args.source,
            "repository": source["repository"],
            "upstream_ref": source["ref"],
            "base_commit": commit,
            "path": paths[0],
            "include": paths[1:],
            "local": local,
            "notes": args.notes,
            "baseline_digest": hashlib.sha256(packed.read_bytes()).hexdigest(),
        }
        packed.rename(target)
        try:
            manifest["imports"][identifier] = entry
            atomic_write(MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())
        except OSError:
            target.unlink()
            raise
    print("Pinned {} at {} ({}). Local files were not changed.".format(identifier, commit, paths[0]))


def inspect(args, manifest):
    selected = [args.id] if args.id else list(manifest["imports"])
    if not selected:
        print("No tracked imports yet. Use track when adapting your first skill.")
        return
    fetched = {}
    for identifier in selected:
        entry = manifest["imports"][identifier]
        with baseline(identifier, entry) as base:
            print("{} -> {}".format(identifier, entry["local"]))
            print("Repository: {}\nBase commit: {}".format(entry["repository"], entry["base_commit"]))
            if args.command == "diff" and args.local:
                local = REPO / relative_path(entry["local"])
                if not local.exists():
                    raise ValueError("Local customization does not exist: {}".format(local))
                print("Comparison: original upstream -> local customization (primary path only)")
                print(compare(base / relative_path(entry["path"]), local) or "No local differences.")
                continue
            ref = getattr(args, "ref", None) or entry["upstream_ref"]
            key = (entry["repository"], ref)
            if key not in fetched:
                fetched[key] = fetch(*key)
            cache, commit = fetched[key]
            print("Current commit: {}\nComparison: original upstream -> current upstream".format(commit))
            with tempfile.TemporaryDirectory(prefix="astack-upstream-") as temporary:
                current = Path(temporary) / "current"
                snapshot(cache, commit, [entry["path"], *entry["include"]], current)
                print(compare(base, current, summary=args.command == "check") or "No upstream changes in tracked paths.")


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise ValueError("Unsupported upstream manifest version")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show configured sources and pinned imports; no network access")
    pin = commands.add_parser("track", help="record an origin and snapshot; does not copy into skills/")
    pin.add_argument("id", help="unique provenance ID; multiple IDs can point to one local skill")
    pin.add_argument("--source", required=True, choices=list(manifest["sources"]))
    pin.add_argument("--path", required=True, help="primary path relative to the upstream repository root")
    pin.add_argument("--include", action="append", default=[], help="additional upstream dependency/license path; repeatable")
    pin.add_argument("--local", required=True, help="local customization file or directory, possibly not created yet")
    pin.add_argument("--ref", help="base commit/tag/ref; defaults to the source's configured branch")
    pin.add_argument("--notes", default="", help="intentional differences or adaptation rationale")
    check = commands.add_parser("check", help="fetch current upstream and summarize path changes; keeps pins unchanged")
    check.add_argument("id", nargs="?")
    diff = commands.add_parser("diff", help="show the full upstream or local customization diff")
    diff.add_argument("id")
    comparison = diff.add_mutually_exclusive_group()
    comparison.add_argument("--local", action="store_true", help="compare the primary baseline path with local files; offline")
    comparison.add_argument("--ref", help="compare to a specific upstream commit/tag/ref instead of the configured branch")
    args = parser.parse_args()
    if args.command == "list":
        for name, source in manifest["sources"].items():
            print("{}: {} ({})".format(name, source["repository"], source["ref"]))
        for identifier, entry in manifest["imports"].items():
            print("{}: {} -> {} @ {}".format(identifier, entry["source"], entry["local"], entry["base_commit"]))
        if not manifest["imports"]:
            print("No tracked imports yet.")
    elif (args.command == "diff" and args.local) or (args.command == "check" and not manifest["imports"]):
        inspect(args, manifest)
    else:
        with exclusive_operation():
            # Another importer may have completed since argument parsing started.
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            if args.command == "track":
                track(args, manifest)
            else:
                inspect(args, manifest)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("astack upstream: {}".format(error), file=sys.stderr)
        sys.exit(1)
