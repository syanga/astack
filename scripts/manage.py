#!/usr/bin/env python3
"""Deploy personal instructions, skills, and selected harness settings."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import astack_settings


def fingerprint(data, mode):
    return hashlib.sha256(data + str(mode).encode()).hexdigest()


def current_hash(path):
    # Never follow a destination symlink to read or overwrite another file.
    if path.is_symlink():
        return "symlink"
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Expected a file at {}".format(path))
    return fingerprint(path.read_bytes(), stat.S_IMODE(path.stat().st_mode))


def destination_identity(path):
    # Resolve directory aliases, but preserve a leaf symlink that we replace itself.
    return str(path.parent.resolve() / path.name)


def validate_parents(path):
    for parent in path.parents:
        if (parent.exists() or parent.is_symlink()) and not parent.is_dir():
            raise ValueError("Destination parent is not a directory: {}".format(parent))


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".astack-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def destination(spec, home, use_env):
    override = os.environ.get(spec.get("env", "")) if use_env else None
    base = Path(override).expanduser() if override else home / spec["default"]
    if not base.is_absolute():
        raise ValueError("Configuration directories must be absolute: {}".format(base))
    return base / spec["suffix"]


# Descriptions are loaded into every session; keep them short pointers, not summaries.
DESCRIPTION_LIMIT = 200
# Codex spells "user-invoked" in a sidecar file; generated from the frontmatter flag when absent.
CODEX_POLICY = "policy:\n  allow_implicit_invocation: false\n"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def check_links(path, text):
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#", "/")):
            continue
        if not (path.parent / target.split("#", 1)[0]).exists():
            raise ValueError("Skill link target is missing: {} -> {}".format(path, target))


def skill_files():
    result = []
    for folder in sorted((REPO / "skills").iterdir()):
        if folder.name.startswith("."):
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Expected a skill directory: {}".format(folder))
        name = folder.name
        if len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Invalid skill directory name: {}".format(name))
        entry = folder / "SKILL.md"
        content = entry.read_text(encoding="utf-8")
        frontmatter = content.split("---", 2)
        if len(frontmatter) != 3 or frontmatter[0].strip():
            raise ValueError("Missing YAML frontmatter: {}".format(entry))
        # Basic portable format check; full YAML parsing is left to each harness.
        fields = {}
        for line in frontmatter[1].splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key] = value.strip().strip("\"'")
        if fields.get("name") != name or not fields.get("description"):
            raise ValueError("Skill needs a matching name and description: {}".format(entry))
        if len(fields["description"]) > DESCRIPTION_LIMIT:
            raise ValueError("Skill description exceeds {} characters: {}".format(DESCRIPTION_LIMIT, entry))
        user_invoked = fields.get("disable-model-invocation") == "true"
        policy = folder / "agents/openai.yaml"
        if policy.exists():
            restricted = "allow_implicit_invocation: false" in policy.read_text(encoding="utf-8")
            if restricted != user_invoked:
                raise ValueError("agents/openai.yaml disagrees with disable-model-invocation: {}".format(policy))
        elif user_invoked:
            result.append((policy.relative_to(REPO / "skills"), CODEX_POLICY.encode(), 0o644))
        for source in sorted(folder.rglob("*")):
            if source.is_symlink():
                raise ValueError("Skill source symlinks are unsupported: {}".format(source))
            if source.is_file():
                relative = source.relative_to(REPO / "skills")
                if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                    continue
                if source.suffix in (".pyc", ".pyo"):
                    continue
                if source.suffix == ".md":
                    check_links(source, source.read_text(encoding="utf-8"))
                result.append((relative, source.read_bytes(), stat.S_IMODE(source.stat().st_mode)))
    return result


def desired_files(harness, home, use_env, skills):
    shared = (REPO / "instructions/AGENTS.md").read_text(encoding="utf-8").strip()
    overlay = (REPO / "instructions" / harness["overlay"]).read_text(encoding="utf-8").strip()
    instructions = "<!-- Generated by astack. Edit the source repository and reinstall. -->\n\n"
    instructions += shared + "\n\n" + overlay + "\n"
    result = {str(destination(harness["instructions"], home, use_env)): (instructions.encode(), 0o644)}
    skills_root = destination(harness["skills"], home, use_env)
    for relative, data, mode in skills:
        result[str(skills_root / relative)] = (data, mode)
    return result


def settings_operations(harnesses, selected, state, home, use_env, args):
    operations = []
    for target in selected:
        old = state.get("settings", {}).get(target, {})
        spec = harnesses[target].get("settings")
        desired = {}
        if args.command == "install" and spec:
            source = REPO / "settings" / spec["source"]
            if source.is_symlink():
                raise ValueError("Settings source symlinks are unsupported: {}".format(source))
            values = astack_settings.json_loads(source.read_text(encoding="utf-8"))
            if list(astack_settings.leaves(values)):
                desired[str(destination(spec, home, use_env))] = (spec["format"], values)
            if old and str(destination(spec, home, use_env)) not in old:
                raise ValueError("{} settings destination changed; uninstall this target before reinstalling".format(target))
        for filename in sorted(set(old) | set(desired)):
            path = Path(filename)
            validate_parents(path)
            actual = current_hash(path)
            if actual == "symlink":
                raise ValueError("Settings destination symlinks are unsupported, even with --force: {}".format(path))
            data = path.read_bytes() if path.exists() else b""
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            previous = old.get(filename)
            format_name, values = desired.get(filename, (previous["format"] if previous else None, {}))
            try:
                rendered, record, changes, conflicts = astack_settings.plan(
                    format_name, data.decode("utf-8"), values, previous, path.exists(), args.force)
            except ValueError as error:
                # Do not echo invalid configuration content (it may contain secrets).
                raise ValueError("Cannot merge settings at {} ({})".format(path, type(error).__name__)) from None
            operations.append((target, path, actual, data, mode, rendered, record, changes, conflicts))
    return operations


def main():
    harnesses = json.loads((REPO / "harnesses.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall"))
    parser.add_argument("--target", action="append", choices=list(harnesses) + ["all"],
                        help="repeat for multiple harnesses; default: claude and codex")
    parser.add_argument("--home", type=Path, help="alternate home; ignores configuration environment overrides")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing anything")
    parser.add_argument("--force", action="store_true", help="back up conflicting files before replacing/removing them")
    args = parser.parse_args()
    selected = args.target or ["claude", "codex"]
    selected = list(harnesses) if "all" in selected else list(dict.fromkeys(selected))
    home = (args.home or Path.home()).expanduser().resolve()
    use_env = args.home is None
    state_root = destination({"default": ".local/state", "env": "XDG_STATE_HOME", "suffix": "astack"}, home, use_env)
    state_path = state_root / "manifest.json"
    # Reject a blocked state directory before changing any managed files.
    validate_parents(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"version": 1, "targets": {}}
    if state.get("version") not in (1, 2):
        raise ValueError("Unsupported astack manifest version")
    state["version"] = 2

    skills = skill_files() if args.command == "install" else []
    settings = settings_operations(harnesses, selected, state, home, use_env, args)
    operations = []
    conflicts = []
    for target in selected:
        old = state["targets"].get(target, {})
        desired = desired_files(harnesses[target], home, use_env, skills) if args.command == "install" else {}
        # Changing an environment override should not silently migrate a previous install.
        if args.command == "install" and old:
            instruction_path = str(destination(harnesses[target]["instructions"], home, use_env))
            if instruction_path not in old:
                raise ValueError("{} destination changed; uninstall this target before reinstalling".format(target))
        for filename in sorted(set(old) | set(desired)):
            path = Path(filename)
            validate_parents(path)
            actual = current_hash(path)
            expected = fingerprint(*desired[filename]) if filename in desired else None
            # An unmanaged file remains a conflict even when its contents match.
            conflict = actual is not None and (filename not in old or actual != old[filename])
            if conflict:
                conflicts.append(filename)
            action = "write" if filename in desired else "remove"
            if actual == expected and not conflict:
                action = "keep"
            operations.append((target, path, action, desired.get(filename), expected, conflict))

    # Keep ownership unambiguous when custom roots or new adapters share paths.
    owners = {}
    for target, records in state["targets"].items():
        for filename in records:
            owners.setdefault(destination_identity(Path(filename)), set()).add(target)
    for target, path, action, payload, expected, conflict in operations:
        if payload is not None:
            owners.setdefault(destination_identity(path), set()).add(target)
    for target, records in state.get("settings", {}).items():
        for filename in records:
            owners.setdefault(destination_identity(Path(filename)), set()).add(target + " settings")
    for target, path, *_ in settings:
        owners.setdefault(destination_identity(path), set()).add(target + " settings")
    for filename, targets in owners.items():
        if len(targets) > 1:
            raise ValueError("Targets must use distinct destinations: {} ({})".format(
                filename, ", ".join(sorted(targets))))

    for target, path, actual, data, mode, rendered, record, changes, key_conflicts in settings:
        conflicts.extend("{} [{}]".format(path, key) for key in key_conflicts)
    if conflicts and not args.force:
        raise ValueError("Existing or locally modified files:\n  " + "\n  ".join(conflicts) +
                         "\nReconcile them, or use --force to back them up first.")

    backup_root = state_root / "backups" / uuid.uuid4().hex
    # Check all shared settings files again before starting any mutation.
    for target, path, actual, *_ in settings:
        if current_hash(path) != actual:
            raise ValueError("Settings changed during preview; retry: {}".format(path))
    for target, path, action, payload, expected, conflict in operations:
        if conflict:
            backup = backup_root / str(path).lstrip("/")
            print("BACKUP {} -> {}".format(path, backup))
            if not args.dry_run:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(backup))
        print("{} {}".format(action.upper(), path))
        if args.dry_run:
            continue
        if action == "write":
            atomic_write(path, *payload)
        elif action == "remove" and (path.exists() or path.is_symlink()):
            path.unlink()
        records = state["targets"].setdefault(target, {})
        if expected is None:
            records.pop(str(path), None)
        else:
            records[str(path)] = expected
        if not records:
            state["targets"].pop(target, None)
        # Record each completed operation. This is not a transaction across files:
        # interruption between a payload change and this write needs reconciliation.
        atomic_write(state_path, (json.dumps(state, indent=2) + "\n").encode(), 0o600)

    for target, path, actual, data, mode, rendered, record, changes, key_conflicts in settings:
        if current_hash(path) != actual:
            raise ValueError("Settings changed during installation; retry: {}".format(path))
        if key_conflicts:
            backup = backup_root / str(path).lstrip("/")
            print("BACKUP {} -> {}".format(path, backup))
            if not args.dry_run and actual is not None:
                atomic_write(backup, data, mode)
        action = "REMOVE" if rendered is None else "MERGE" if rendered.encode() != data else "KEEP"
        print("{} SETTINGS {}{}".format(action, path, " [" + ", ".join(changes) + "]" if changes else ""))
        if args.dry_run:
            continue
        old_settings = state.get("settings", {})
        new_settings = {name: dict(records) for name, records in old_settings.items()}
        records = new_settings.setdefault(target, {})
        if record is None:
            records.pop(str(path), None)
        else:
            records[str(path)] = record
        if not records:
            new_settings.pop(target, None)
        if action == "MERGE":
            atomic_write(path, rendered.encode(), mode)
        elif action == "REMOVE" and path.exists():
            path.unlink()
        state["settings"] = new_settings
        try:
            atomic_write(state_path, (json.dumps(state, indent=2) + "\n").encode(), 0o600)
        except OSError:
            # Restore the settings payload if its ownership record could not be saved.
            state["settings"] = old_settings
            if actual is None:
                if path.exists():
                    path.unlink()
            else:
                atomic_write(path, data, mode)
            raise
    print("{} complete for {}.".format("Preview" if args.dry_run else args.command.capitalize(), ", ".join(selected)))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print("astack: {}".format(error), file=sys.stderr)
        sys.exit(1)
