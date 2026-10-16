#!/usr/bin/env python3
"""Install, preview, update, and remove the optional claude-router component.

The component is a Go binary built from the pinned source, a per-user service
descriptor, and a state directory. Installing it never changes client settings.
"""

import argparse
from contextlib import redirect_stdout
import fcntl
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import astack_files as files
import astack_settings

NAME = "claude-router"
DEFAULT_SERVICE = "dev.astack.claude-router"
DEFAULT_PORT = 18787
MIN_GO = (1, 26, 0)
MANAGERS = ("launchd", "systemd", "foreground")
CLIENT_KEYS = ("apiKeyHelper", "env.ANTHROPIC_BASE_URL", "env.CLAUDE_CODE_RETRY_WATCHDOG")
BEFORE_HELPER = ("env.ANTHROPIC_AUTH_TOKEN", "env.ANTHROPIC_API_KEY")
SECRET_KEY = re.compile(r"TOKEN|KEY|SECRET|PASSWORD", re.IGNORECASE)
HELPER = ("claude_router.py", "astack_files.py", "astack_settings.py")


def repo():
    return SCRIPTS.parent


def env_dir(variable, default, home, use_env):
    override = os.environ.get(variable) if use_env else None
    base = Path(override).expanduser() if override else home / default
    if not base.is_absolute():
        raise ValueError("Configuration directories must be absolute: {}".format(base))
    return base


@dataclass(frozen=True)
class Layout:
    home: Path
    data: Path
    state: Path
    manifest: Path
    manager: str
    service: str
    descriptor: Path | None
    port: int

    @property
    def binary(self):
        return self.data / "bin" / NAME

    @property
    def lib(self):
        return self.data / "lib"

    @property
    def config(self):
        return self.state / "config.json"

    @property
    def token(self):
        return self.state / "client-token"

    def record(self):
        return {"data": str(self.data), "state": str(self.state), "manager": self.manager,
                "service": self.service, "descriptor": str(self.descriptor) if self.descriptor else None}


def layout(home, use_env, state_root, manager=None, service=None, port=None):
    """Derive every component path from the home and the astack state root."""
    if manager is None:
        manager = {"darwin": "launchd", "linux": "systemd"}.get(sys.platform)
    if manager not in MANAGERS:
        raise ValueError("claude-router supports macOS and Linux; this host is {}".format(sys.platform))
    service = service or DEFAULT_SERVICE
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", service):
        raise ValueError("Invalid service name: {}".format(service))
    port = DEFAULT_PORT if port is None else port
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535: {}".format(port))
    descriptor = None
    if manager == "launchd":
        descriptor = home / "Library/LaunchAgents" / (service + ".plist")
    elif manager == "systemd":
        descriptor = env_dir("XDG_CONFIG_HOME", ".config", home, use_env) / "systemd/user" / (service + ".service")
    data = env_dir("XDG_DATA_HOME", ".local/share", home, use_env) / "astack" / NAME
    return Layout(home=home, data=data, state=state_root / NAME, manifest=state_root / "manifest.json",
                  manager=manager, service=service, descriptor=descriptor, port=port)


def go_version(text):
    match = re.fullmatch(r"go(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        raise ValueError("Unrecognized Go version {!r}; claude-router needs go{}.{}.{} or later".format(text, *MIN_GO))
    return tuple(int(part or 0) for part in match.groups())


def host_platform():
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return sys.platform, arch


def check_go():
    """Return the Go command after checking its version and target platform."""
    go = shutil.which("go")
    if go is None:
        raise ValueError("claude-router needs Go {}.{}.{} or later on PATH".format(*MIN_GO))
    result = subprocess.run([go, "env", "GOVERSION", "GOOS", "GOARCH", "GOHOSTOS", "GOHOSTARCH"],
                            capture_output=True, text=True)
    lines = result.stdout.split()
    if result.returncode != 0 or len(lines) != 5:
        raise ValueError("go env failed: {}".format(result.stderr.strip()))
    version, goos, goarch, hostos, hostarch = lines
    if go_version(version) < MIN_GO:
        raise ValueError("claude-router needs Go {}.{}.{} or later; found {}".format(*MIN_GO, version))
    host = host_platform()
    if (goos, goarch) != (hostos, hostarch) or (hostos, hostarch) != host:
        raise ValueError("Go would build for {}/{}, but this host is {}/{}; unset GOOS and GOARCH or use a native Go".format(
            goos, goarch, *host))
    return go


def source_revision():
    """Return the checkout's short commit, with +dirty for local component changes, or unknown."""
    component = repo() / "components" / NAME
    head = subprocess.run(["git", "-C", str(component), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        return "unknown"
    dirty = subprocess.run(["git", "-C", str(component), "status", "--porcelain", "--", "."], capture_output=True, text=True)
    return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")


def build(go, directory):
    """Build the binary from the pinned dependency cache into directory."""
    output = directory / NAME
    env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOFLAGS="-mod=readonly", CGO_ENABLED="0")
    revision = "-X github.com/syanga/astack/components/claude-router/internal/claude.linkedRevision=" + source_revision()
    result = subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-ldflags=" + revision, "-o", str(output), "./cmd/claude-router"],
                            cwd=str(repo() / "components" / NAME), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n  ".join(result.stderr.strip().splitlines()[-8:])
        raise ValueError("claude-router build failed:\n  " + tail)
    return output.read_bytes()


def systemd_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def xml_escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def descriptor_text(lay):
    template = (repo() / "components" / NAME / "service" / (lay.manager + (".plist" if lay.manager == "launchd" else ".service"))).read_text(encoding="utf-8")
    quote = xml_escape if lay.manager == "launchd" else systemd_quote
    values = {"name": lay.service, "binary": quote(str(lay.binary)), "config": quote(str(lay.config)),
              "log": quote(str(lay.state / "service.log")), "home": quote(str(lay.home)),
              "home_env": quote("HOME=" + str(lay.home)),
              "state": quote(str(lay.state)) if lay.manager == "launchd" else str(lay.state).replace("%", "%%")}
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def helper_files():
    result = {}
    for name in HELPER:
        source = SCRIPTS / name
        result[name] = (source.read_bytes(), 0o755 if name == "claude_router.py" else 0o644)
    vendor = SCRIPTS / "_vendor"
    for source in sorted(vendor.rglob("*")):
        relative = source.relative_to(SCRIPTS)
        if source.is_file() and "__pycache__" not in relative.parts and source.suffix not in (".pyc", ".pyo"):
            result[str(relative)] = (source.read_bytes(), 0o644)
    return result


def desired_files(lay, binary):
    record = {"manager": lay.manager, "name": lay.service if lay.descriptor else None,
              "descriptor": str(lay.descriptor) if lay.descriptor else None, "config": str(lay.config),
              "helper": str(lay.lib / "claude_router.py"), "python": sys.executable, "manifest": str(lay.manifest)}
    result = {
        str(lay.binary): (binary, 0o755),
        str(lay.state / "service.json"): ((json.dumps({k: v for k, v in record.items() if v}, indent=2) + "\n").encode(), 0o600),
    }
    for relative, payload in helper_files().items():
        result[str(lay.lib / relative)] = payload
    if lay.descriptor:
        result[str(lay.descriptor)] = (descriptor_text(lay).encode(), 0o644)
    return result


def seed_files(lay):
    """Files created once and then owned by the user: the router configuration."""
    config = {"listen": "127.0.0.1:{}".format(lay.port), "state_dir": str(lay.state),
              "client_token_file": str(lay.token), "accounts": []}
    return {str(lay.config): ((json.dumps(config, indent=2) + "\n").encode(), 0o600)}


def router_running(lay):
    """Report whether a process holds the router's assignment lock."""
    lock = lay.state / "assignments" / "lock"
    if not lock.exists():
        return False
    with open(lock, "r+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False


@dataclass
class Plan:
    command: str
    lay: Layout
    old: dict
    operations: list = field(default_factory=list)
    seeds: list = field(default_factory=list)
    settings: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    binary_changed: bool = False

    def paths(self):
        return [path for path, *_ in self.operations] + [path for path, *_ in self.seeds]


def plan(command, state, lay, force=False):
    """Compute the component's changes without writing anything."""
    old = state.get("components", {}).get(NAME, {})
    if command == "install" and old.get("layout") and old["layout"] != lay.record():
        raise ValueError("{} destination changed; uninstall the component before reinstalling".format(NAME))
    if old.get("layout"):
        recorded = old["layout"]
        lay = Layout(home=lay.home, data=Path(recorded["data"]), state=Path(recorded["state"]), manifest=lay.manifest,
                     manager=recorded["manager"], service=recorded["service"],
                     descriptor=Path(recorded["descriptor"]) if recorded["descriptor"] else None, port=lay.port)
    result = Plan(command=command, lay=lay, old=old)
    desired, seeds = {}, {}
    if command == "install":
        go = check_go()
        with tempfile.TemporaryDirectory(prefix="astack-claude-router-") as directory:
            binary = build(go, Path(directory))
        desired = desired_files(lay, binary)
        seeds = seed_files(lay)
    managed = dict(old.get("files", {}))
    for filename, expected in old.get("pending", {}).items():
        if files.current_hash(Path(filename)) == expected:
            managed[filename] = expected
    for filename in sorted(set(managed) | set(desired)):
        path = Path(filename)
        files.validate_parents(path)
        actual = files.current_hash(path)
        expected = files.fingerprint(*desired[filename]) if filename in desired else None
        conflict = actual is not None and (filename not in managed or actual != managed[filename])
        if conflict:
            result.conflicts.append(filename)
        action = "write" if filename in desired else "remove"
        if actual == expected and not conflict:
            action = "keep"
        if filename == str(lay.binary) and action == "write":
            result.binary_changed = True
        result.operations.append((path, action, desired.get(filename), expected, conflict))
    created = old.get("seeds", {})
    for filename in sorted(set(created) | set(seeds)):
        path = Path(filename)
        files.validate_parents(path)
        actual = files.current_hash(path)
        if command == "install":
            action = "create" if actual is None else "keep"
            payload = seeds.get(filename)
        else:
            action = "remove" if actual is not None and actual == created.get(filename) else "keep"
            payload = None
        result.seeds.append((path, action, payload, created.get(filename)))
    if command == "uninstall":
        previous = state.get("settings", {}).get(NAME, {})
        result.settings = astack_settings.prepare(NAME, previous, {}, force)
        result.conflicts.extend("{} [{}]".format(change.path, key) for change in result.settings for key in change.conflicts)
    return result


def check_harness_settings(harness_values):
    names = {".".join(path) for path, _ in astack_settings.leaves(harness_values)}
    shared = sorted(names & set(CLIENT_KEYS + BEFORE_HELPER))
    if shared:
        raise ValueError("Harness settings must not set keys that claude-router activation owns or overrides: " + ", ".join(shared))


def client_settings(state_dir, current):
    present = [key for key in BEFORE_HELPER if astack_settings.lookup(current, tuple(key.split(".")))["exists"]]
    if present:
        raise ValueError("Client settings set {}, which Claude Code uses instead of the router's apiKeyHelper; remove it first".format(
            ", ".join(present)))
    config = json.loads((state_dir / "config.json").read_text(encoding="utf-8"))
    token_file = Path(state_dir).absolute() / config["client_token_file"]
    values = {"apiKeyHelper": "cat " + shlex.quote(str(token_file)),
              "env": {"ANTHROPIC_BASE_URL": "http://" + config["listen"], "CLAUDE_CODE_RETRY_WATCHDOG": "1"}}
    replaced = []
    for key in CLIENT_KEYS:
        path = tuple(key.split("."))
        found = astack_settings.lookup(current, path)
        if found["exists"] and not astack_settings.same(found, astack_settings.lookup(values, path)):
            replaced.append(key)
    return values, replaced


def stop_service(lay, dry_run):
    """Stop a supervised service through the installed binary before removal."""
    if lay.manager == "foreground" or not lay.binary.exists():
        return
    print("STOP {} {}".format(lay.manager, lay.service))
    if dry_run:
        return
    result = subprocess.run([str(lay.binary), "stop", "-uninstall", "-state", str(lay.state)], capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError("Could not stop the router: {}".format((result.stderr or result.stdout).strip()))
    print(result.stdout, end="")


def apply(p, state, state_path, backup_root, dry_run=False):
    """Apply a plan, recording each completed write in the manifest."""
    lay = p.lay
    if p.command == "uninstall" and not p.old and not p.settings:
        return
    if p.command == "uninstall":
        stop_service(lay, dry_run)
    elif not dry_run:
        lay.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = state.setdefault("components", {}).setdefault(NAME, {})
    record.setdefault("files", {}).update(
        {name: value for name, value in record.get("pending", {}).items() if files.current_hash(Path(name)) == value})
    record["pending"] = {}
    if p.command == "install":
        record["layout"] = lay.record()

    def save():
        if not dry_run:
            files.save_manifest(state_path, state)

    for path, action, payload, expected, conflict in p.operations:
        if conflict:
            backup = backup_root / str(path).lstrip("/")
            print("BACKUP {} -> {}".format(path, backup))
            if not dry_run:
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(backup))
        print("{} {}".format(action.upper(), path))
        if dry_run:
            continue
        if action == "write":
            record["pending"][str(path)] = expected
            save()
            files.atomic_write(path, *payload)
        elif action == "remove" and (path.exists() or path.is_symlink()):
            path.unlink()
        record["pending"].pop(str(path), None)
        if expected is None:
            record["files"].pop(str(path), None)
        else:
            record["files"][str(path)] = expected
        save()
    for path, action, payload, created in p.seeds:
        print("{} {}".format(action.upper(), path))
        if dry_run:
            continue
        seeds = record.setdefault("seeds", {})
        if action == "create":
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, payload[1])
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload[0])
            seeds[str(path)] = files.fingerprint(*payload)
        elif action == "remove":
            path.unlink()
            seeds.pop(str(path), None)
        elif p.command == "uninstall":
            seeds.pop(str(path), None)
        save()
    if p.command == "install":
        if not lay.token.exists():
            print("CREATE {}".format(lay.token))
            if not dry_run:
                fd = os.open(str(lay.token), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(secrets.token_hex(32) + "\n")
        if p.binary_changed and router_running(lay):
            print("RESTART needed to run the new binary: claude-router stop; claude-router start")
    for change in p.settings:
        change.apply(state, state_path, backup_root, dry_run=dry_run)
    if p.command == "uninstall":
        print("KEEP {} (logins, assignments, client token, events)".format(lay.state))
        if not dry_run:
            state.get("components", {}).pop(NAME, None)
            for cache in sorted(lay.lib.rglob("__pycache__")) if lay.lib.is_dir() else []:
                shutil.rmtree(cache)
            for directory in (lay.lib / "_vendor" / "tomllib", lay.lib / "_vendor", lay.lib, lay.binary.parent, lay.data):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
    elif not dry_run:
        commit = subprocess.run(["git", "-C", str(repo()), "rev-parse", "HEAD"], capture_output=True, text=True)
        record["commit"] = commit.stdout.strip() if commit.returncode == 0 else None
    save()


def shown(path):
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


def describe(value):
    if not value.get("exists"):
        return "unset"
    return json.dumps(value["value"]) if not isinstance(value["value"], str) else value["value"]


def other_keys(data, owned):
    if not isinstance(data, dict):
        return 0
    return sum(1 for path, _ in astack_settings.leaves(data) if json.dumps(path) not in owned)


def router_inventory(state_dir):
    logins = sorted(path.stem for path in (state_dir / "auths").glob("*.json"))
    conversations = set()
    journal = state_dir / "assignments" / "assignments.jsonl"
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                conversations.add(json.loads(line)["c"])
            except (ValueError, KeyError, TypeError):
                continue
    return "router logins {}; {} assignments".format(", ".join(logins) or "none", len(conversations))


NOTHING_OWNED = 3


def direct_backup(manifest_path, filename):
    digest = hashlib.sha256(Path(filename).read_bytes()).hexdigest()[:12]
    return manifest_path.parent / "backups" / ("direct-" + digest) / Path(filename).name


def direct(state_dir, manifest_path, mode, stopped=False):
    state = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    owned = state.get("settings", {}).get(NAME, {})
    inventory = router_inventory(state_dir)
    if not owned:
        if mode == "check":
            return NOTHING_OWNED
        print("keep    no client settings name the router; " + inventory)
        print("ready   nothing to change")
        return 0
    lines, skipped, pending, others, needs_login = [], [], {}, 0, False
    for filename, record in sorted(owned.items()):
        current = astack_settings.document(record["format"], Path(filename).read_text(encoding="utf-8") if Path(filename).exists() else "")
        keys = {}
        for key, entry in sorted(record["keys"].items()):
            path = tuple(json.loads(key))
            label = ".".join(path)
            found = current.get(path)
            if not astack_settings.same(found, entry["installed"]):
                if astack_settings.same(found, entry["before"]):
                    skipped.append("skip    {} {} (already restored)".format(shown(filename), label))
                    continue
                print("claude-router: direct: {} changed after activation".format(label), file=sys.stderr)
                print("        expected {}, found {}".format(redact(label, entry["installed"]), redact(label, found)), file=sys.stderr)
                print("next    restore or remove the key, then run again (local edits are kept)", file=sys.stderr)
                return 1
            keys[key] = entry
            verb = "restore" if entry["before"].get("exists") else "remove "
            lines.append("{} {} {} ({})".format(verb, shown(filename), label, redact(label, entry["installed"])))
            needs_login = needs_login or (label == "apiKeyHelper" and not entry["before"].get("exists"))
        pending[filename] = dict(record, keys=keys)
        others += other_keys(current.data, record["keys"])
    if mode == "check":
        return 0
    backups = {filename: direct_backup(manifest_path, filename) for filename, record in pending.items() if record["keys"]}
    for line in lines + skipped:
        print(line)
    print("keep    {} other settings keys; {}".format(others, inventory))
    for filename in sorted(backups):
        print("backup  {}".format(shown(backups[filename])))
    if needs_login:
        print("after   claude needs its own /login")
    if mode == "prepare":
        print("ready   nothing changed; apply with claude-router direct")
        return 0
    changes = astack_settings.prepare(NAME, pending, {}, False)
    for filename, backup in backups.items():
        files.atomic_write(backup, Path(filename).read_bytes(), 0o600)
    with redirect_stdout(io.StringIO()):
        for change in changes:
            change.apply(state, manifest_path, manifest_path.parent / "backups" / uuid.uuid4().hex)
    print("done    clients connect directly; the router stays installed" + (" and stopped" if stopped else ""))
    return 0


def redact(label, entry):
    if isinstance(entry, dict) and "exists" in entry:
        if not entry["exists"]:
            return "unset"
        entry = entry.get("value")
    return "[redacted]" if SECRET_KEY.search(label.rsplit(".", 1)[-1]) else (entry if isinstance(entry, str) else json.dumps(entry))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "uninstall"):
        p = sub.add_parser(command)
        p.add_argument("--home", type=Path, help="alternate home; ignores configuration environment overrides")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--manager", choices=MANAGERS, help="service manager; default launchd on macOS, systemd on Linux")
        p.add_argument("--service", help="launchd label or systemd unit name; default " + DEFAULT_SERVICE)
        p.add_argument("--port", type=int, help="loopback port for a new configuration; default {}".format(DEFAULT_PORT))
    d = sub.add_parser("direct")
    d.add_argument("--state", type=Path, required=True)
    d.add_argument("--manifest", type=Path, required=True)
    mode = d.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--check", action="store_true")
    d.add_argument("--stopped", action="store_true", help="the caller stopped the service")
    args = parser.parse_args()
    if args.command == "direct":
        return direct(args.state, args.manifest, "prepare" if args.prepare else "check" if args.check else "apply", args.stopped)
    home = (args.home or Path.home()).expanduser().resolve()
    use_env = args.home is None
    state_root = env_dir("XDG_STATE_HOME", ".local/state", home, use_env) / "astack"
    run(args.command, state_root, layout(home, use_env, state_root, args.manager, args.service, args.port), args)
    return 0


def load_state(state_root):
    state_path = state_root / "manifest.json"
    files.validate_parents(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"version": 1, "targets": {}}
    if state.get("version") not in (1, 2, 3):
        raise ValueError("Unsupported astack manifest version")
    return state_path, state


def run(command, state_root, lay, args):
    state_path, state = load_state(state_root)
    p = plan(command, state, lay, args.force)
    check_targets(state, p)
    finish(p, state, state_path, state_root, args)


def check_targets(state, p):
    """Refuse component paths that a harness target already manages."""
    owned = {str(Path(name).parent.resolve() / Path(name).name) for records in state.get("targets", {}).values() for name in records}
    for path in p.paths():
        if str(path.parent.resolve() / path.name) in owned:
            raise ValueError("Targets must use distinct destinations: {} ({})".format(path, NAME))


def finish(p, state, state_path, state_root, args):
    if p.conflicts and not args.force:
        raise ValueError("Existing or locally modified files:\n  " + "\n  ".join(p.conflicts) +
                         "\nReconcile them, or use --force to back them up first.")
    for change in p.settings:
        change.check_unchanged("preview")
    apply(p, state, state_path, state_root / "backups" / uuid.uuid4().hex, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print("astack: {}".format(error), file=sys.stderr)
        sys.exit(1)
