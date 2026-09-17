#!/usr/bin/env python3
"""Install/remove an optional per-repository Gitleaks pre-push guard."""

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from gitleaks_pre_push import MARKER, PAYLOAD, VERSION


HERE = Path(__file__).resolve().parent
ORIGINAL = "pre-push.astack-original"
GSTACK_MARKER = b"# gstack-redact pre-push (managed)"
WRAPPER = ("#!/usr/bin/env python3\n" + MARKER + "\n"
           "from pathlib import Path\nimport runpy\n"
           "runpy.run_path(str(Path(__file__).resolve().parent / 'astack-gitleaks' / 'runner.py'), "
           "run_name='__main__')\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular file: {}".format(path))
    return {"sha256": digest(path.read_bytes()), "mode": stat.S_IMODE(path.stat().st_mode)}


def hooks_dir(repo):
    def git(*args):
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if result.returncode:
            raise ValueError("Cannot resolve this repository's hooks directory.")
        return result.stdout.strip()
    common = Path(git("rev-parse", "--git-common-dir"))
    common = (repo / common).resolve()
    effective = Path(git("rev-parse", "--git-path", "hooks"))
    effective = repo / effective
    if effective.resolve() != common / "hooks" or effective.is_symlink():
        raise ValueError("Custom core.hooksPath or symlinked hooks directories are unsupported; "
                         "no hooks were changed.")
    return common / "hooks"


@contextmanager
def lock(hooks):
    path = hooks.parent / "astack-hooks.lock"
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError("Another hook operation may be running. If it crashed, remove {} and retry."
                         .format(path)) from None
    try:
        yield
    finally:
        path.rmdir()


def atomic_write(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix=".astack-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def download():
    pins = json.loads((HERE / "gitleaks/releases.json").read_text())
    arch = {"aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(
        platform.machine().lower(), platform.machine().lower())
    key = platform.system().lower() + "_" + arch
    if key not in pins["sha256"] or pins["version"] != VERSION:
        raise ValueError("No pinned download for {}; provide --gitleaks with version {}."
                         .format(key, VERSION))
    asset = "gitleaks_{}_{}.tar.gz".format(VERSION, key)
    url = "https://github.com/gitleaks/gitleaks/releases/download/v{}/{}".format(VERSION, asset)
    print("Download " + url)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if digest(data) != pins["sha256"][key]:
        raise ValueError("Gitleaks download checksum mismatch; no hook installed.")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember("gitleaks")
        if not member.isfile() or member.size > 128 * 1024 * 1024:
            raise ValueError("Invalid executable in Gitleaks release.")
        return archive.extractfile(member).read()


def verify_install(hooks):
    payload = hooks / PAYLOAD
    if payload.is_symlink():
        raise ValueError("Managed hook directory was replaced by a symlink.")
    state = json.loads((payload / "state.json").read_text())
    if state.get("version") != 1:
        raise ValueError("Unsupported hook state version.")
    if record(hooks / "pre-push") != {"sha256": digest(WRAPPER), "mode": 0o755}:
        raise ValueError("Pre-push hook was modified; reconcile it before uninstalling.")
    for name, expected in state["files"].items():
        if Path(name).name != name or record(payload / name) != expected:
            raise ValueError("Managed hook file was modified: {}".format(name))
    if state["original"] is not None and record(hooks / ORIGINAL) != state["original"]:
        raise ValueError("Saved original hook was modified; reconcile it before uninstalling.")
    return state


def install(hooks, binary=None, replace_gstack=False, dry_run=False):
    hook, payload = hooks / "pre-push", hooks / PAYLOAD
    if payload.exists() or payload.is_symlink():
        verify_install(hooks)
        print("Gitleaks hook already installed. Uninstall before changing its version or options.")
        return
    if (hooks / ORIGINAL).exists() or (hooks / ORIGINAL).is_symlink():
        raise ValueError("A saved hook already exists; reconcile it before installing.")
    old = None
    original = None
    chain = None
    if hook.exists() or hook.is_symlink():
        original = record(hook)
        old = hook.read_bytes()
        is_gstack = GSTACK_MARKER in old
        if MARKER.encode() in old:
            raise ValueError("Found an astack hook without its state; reconcile it before installing.")
        if is_gstack and not replace_gstack:
            raise ValueError("Found gstack's guard. Use --replace-gstack to replace it and retain "
                             "a copy for uninstall.")
        if replace_gstack and not is_gstack:
            raise ValueError("--replace-gstack requires a gstack-managed pre-push hook.")
        if is_gstack:
            chain = "pre-push.local"
        elif original["mode"] & 0o111:
            chain = ORIGINAL
    elif replace_gstack:
        raise ValueError("There is no gstack-managed pre-push hook to replace.")
    print("Install Gitleaks {} in {}{}".format(
        VERSION, hooks, " (replace gstack; preserve pre-push.local)" if replace_gstack else ""))
    if dry_run:
        return
    contents = Path(binary).expanduser().read_bytes() if binary else download()
    hooks.mkdir(exist_ok=True)
    # Prepare the complete installation before replacing the active hook.
    with tempfile.TemporaryDirectory(prefix=".astack-stage-", dir=hooks) as tmp:
        stage = Path(tmp)
        (stage / "gitleaks").write_bytes(contents)
        (stage / "gitleaks").chmod(0o755)
        version = subprocess.run([str(stage / "gitleaks"), "version"], capture_output=True,
                                 text=True, timeout=10)
        if version.returncode or version.stdout.strip().removeprefix("v") != VERSION:
            raise ValueError("Expected Gitleaks {}; no hook installed.".format(VERSION))
        shutil.copyfile(HERE / "gitleaks_pre_push.py", stage / "runner.py")
        shutil.copyfile(HERE / "gitleaks/LICENSE", stage / "LICENSE")
        (stage / "rules.toml").write_text("[extend]\nuseDefault = true\n")
        (stage / "ignore").write_text("")
        files = {p.name: record(p) for p in stage.iterdir()}
        (stage / "state.json").write_text(json.dumps({
            "version": 1, "gitleaks_version": VERSION, "chain": chain,
            "original": original, "files": files,
        }, indent=2) + "\n")
        # Detect a concurrent manual change before touching the active hook.
        if (record(hook) if hook.exists() or hook.is_symlink() else None) != original:
            raise ValueError("Pre-push hook changed during preparation; retry.")
        created = False
        saved = False
        try:
            payload.mkdir()
            created = True
            for source in stage.iterdir():
                shutil.copy2(source, payload / source.name)
            if old is not None:
                # Never overwrite a backup created outside our operation.
                with (hooks / ORIGINAL).open("xb") as backup:
                    saved = True
                    backup.write(old)
                    os.fchmod(backup.fileno(), original["mode"])
            atomic_write(hook, WRAPPER, 0o755)
        except BaseException:
            # os.replace may have succeeded before an interruption was delivered.
            # Clean up only when the original hook is demonstrably still intact;
            # otherwise retain the runner and backup for retry or restoration.
            try:
                unchanged = (record(hook) if hook.exists() or hook.is_symlink() else None) == original
            except (OSError, ValueError):
                unchanged = False
            if unchanged:
                if created:
                    shutil.rmtree(payload)
                if saved:
                    (hooks / ORIGINAL).unlink(missing_ok=True)
            raise


def uninstall(hooks, dry_run=False):
    payload = hooks / PAYLOAD
    if not payload.exists() and not payload.is_symlink():
        if (hooks / "pre-push").exists() and MARKER.encode() in (hooks / "pre-push").read_bytes():
            raise ValueError("Found an astack hook without state; reconcile it before uninstalling.")
        print("No astack Gitleaks hook installed.")
        return
    state = verify_install(hooks)
    expected = set(state["files"]) | {"state.json"}
    if {p.name for p in payload.iterdir()} != expected:
        raise ValueError("Unmanaged files in the hook directory; preserve them elsewhere before uninstalling.")
    print("Remove astack Gitleaks hook from {} and restore its predecessor.".format(hooks))
    if dry_run:
        return
    if state["original"] is None:
        (hooks / "pre-push").unlink()
    else:
        os.replace(hooks / ORIGINAL, hooks / "pre-push")
    for name in expected:
        (payload / name).unlink()
    payload.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--gitleaks", type=Path, help="use this trusted executable instead of downloading")
    parser.add_argument("--replace-gstack", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "uninstall" and (args.gitleaks or args.replace_gstack):
        parser.error("--gitleaks and --replace-gstack apply only to install")
    try:
        hooks = hooks_dir(args.repo.expanduser().resolve())
        def perform():
            if args.command == "install":
                install(hooks, args.gitleaks, args.replace_gstack, args.dry_run)
            else:
                uninstall(hooks, args.dry_run)
        if args.dry_run:
            perform()
        else:
            with lock(hooks):
                perform()
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print("astack: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
