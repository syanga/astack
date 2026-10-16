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
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
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
ACTIVATION = "activation.json"
CLIENTS = (("claude_code", "--claude"), ("agent_sdk", "--agent-sdk"))
MANAGED_SETTINGS = {"darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                    "linux": Path("/etc/claude-code/managed-settings.json")}


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
    journal = read_journal(lay.state) if command == "uninstall" and old else None
    if journal and journal["phase"] != "active":
        raise ValueError("An activation of claude-router was interrupted; run claude-router direct, then uninstall")
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
            if (lay.state / ACTIVATION).exists():
                (lay.state / ACTIVATION).unlink()
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
    done = "done    clients connect directly; the router stays installed" + (" and stopped" if stopped else "")
    journal = read_journal(state_dir)
    if journal and journal["phase"] != "active":
        if mode == "check":
            return 0
        if mode == "prepare":
            print("restore {} (interrupted activation)".format(shown(Path(journal["settings"]))))
            print("keep    " + inventory)
            print("ready   nothing changed; apply with claude-router direct")
            return 0
        try:
            settings = undo_interrupted(state_dir, state, manifest_path)
        except Refusal as refusal:
            report("direct", refusal)
            return 1
        print("restore {} (interrupted activation)".format(shown(settings)))
        print("keep    " + inventory)
        print(done)
        return 0
    if not owned:
        if mode == "check":
            return NOTHING_OWNED
        print("keep    no client settings name the router; " + inventory)
        print("ready   nothing to change")
        if mode == "apply" and journal:
            (state_dir / ACTIVATION).unlink()
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
    if journal:
        (state_dir / ACTIVATION).unlink()
    print(done)
    return 0


def redact(label, entry):
    if isinstance(entry, dict) and "exists" in entry:
        if not entry["exists"]:
            return "unset"
        entry = entry.get("value")
    return "[redacted]" if SECRET_KEY.search(label.rsplit(".", 1)[-1]) else (entry if isinstance(entry, str) else json.dumps(entry))


class Refusal(Exception):
    """A refusal printed as the error line, its detail lines, and a next step."""

    def __init__(self, error, details=(), next_step=None):
        super().__init__(error)
        self.details = list(details)
        self.next_step = next_step


def report(command, refusal):
    print("claude-router: {}: {}".format(command, refusal), file=sys.stderr)
    for line in refusal.details:
        print("        " + line, file=sys.stderr)
    if refusal.next_step:
        print("next    " + refusal.next_step, file=sys.stderr)


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def installed_layout(state, state_root, home):
    record = state.get("components", {}).get(NAME, {})
    recorded = record.get("layout")
    if not recorded:
        raise Refusal("claude-router is not installed", next_step="./install.sh --component claude-router")
    lay = Layout(home=home, data=Path(recorded["data"]), state=Path(recorded["state"]), manifest=state_root / "manifest.json",
                 manager=recorded["manager"], service=recorded["service"],
                 descriptor=Path(recorded["descriptor"]) if recorded["descriptor"] else None, port=DEFAULT_PORT)
    return lay, record


def fetch_status(listen, token):
    request = urllib.request.Request("http://{}/claude-router/status".format(listen),
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def router_config(state_dir):
    config = json.loads((state_dir / "config.json").read_text(encoding="utf-8"))
    return config["listen"], Path(state_dir).absolute() / config["client_token_file"]


def running_status(state_dir):
    listen, token_file = router_config(state_dir)
    try:
        return listen, fetch_status(listen, token_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise Refusal("the router at {} does not answer: {}".format(listen, getattr(error, "reason", error)),
                      next_step="claude-router start, then run again") from None


def client_versions(claude, agent_sdk):
    versions = {"claude_code": None}
    try:
        result = subprocess.run([str(claude), "--version"], capture_output=True, text=True, timeout=120)
        words = result.stdout.split()
        versions["claude_code"] = words[0] if result.returncode == 0 and words else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        package = json.loads((agent_sdk / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        package = {}
    versions["agent_sdk"] = package.get("version") if package.get("name") == "@anthropic-ai/claude-agent-sdk" else None
    return versions


def measure(lay, record, status, claude, agent_sdk):
    """Return the identity that an acceptance record must match."""
    return {"head": record.get("commit"), "binary_sha256": file_sha256(lay.binary),
            "running_binary": status.get("binary"), "running_binary_sha256": status.get("binary_sha256"),
            "sdk": status.get("sdk"), "clients": client_versions(claude, agent_sdk)}


def short(value):
    return value[:12] if isinstance(value, str) else "none"


def mismatches(accepted, current):
    found = []
    if accepted.get("result") != "pass":
        found.append("result: {}, not pass".format(accepted.get("result", "missing")))
    head = accepted.get("head")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        found.append("head: the record names no full commit")
    elif head != current["head"]:
        found.append("head: accepted {}, installed {}".format(short(head), short(current["head"])))
    elif current["running_binary"] != head[:7]:
        found.append("head: accepted {}, running binary built from {}".format(head[:7], current["running_binary"]))
    binary = accepted.get("binary_sha256")
    if binary != current["binary_sha256"]:
        found.append("binary: accepted {}, installed {}".format(short(binary), short(current["binary_sha256"])))
    elif binary != current["running_binary_sha256"]:
        found.append("binary: accepted {}, running {}".format(short(binary), short(current["running_binary_sha256"])))
    if accepted.get("sdk") != current["sdk"]:
        found.append("sdk: accepted {}, running {}".format(accepted.get("sdk"), current["sdk"]))
    clients = accepted.get("clients") if isinstance(accepted.get("clients"), dict) else {}
    for key, flag in CLIENTS:
        if not clients.get(key):
            found.append("{}: the record names no version".format(key))
        elif clients[key] != current["clients"].get(key):
            found.append("{}: accepted {}, {} has {}".format(key, clients[key], flag, current["clients"].get(key) or "no version"))
    return found


def usable_accounts(status):
    problems = [] if status.get("journal") == "ok" else ["assignment journal: {}".format(status.get("journal"))]
    usable = [a["id"] for a in status.get("accounts") or []
              if a.get("registered") and not a.get("needs_login") and (a.get("overage") or {}).get("state") == "disabled"]
    if not usable:
        problems.append("no account has a login and a reading that paid overflow is disabled")
    return usable, problems


def launcher_has(name):
    """Report the service manager that gives apps the variable, or None."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["launchctl", "getenv", name], capture_output=True, text=True, timeout=10)
            return "launchd" if result.returncode == 0 and result.stdout.strip() else None
        result = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=10)
        found = result.returncode == 0 and any(line.startswith(name + "=") for line in result.stdout.splitlines())
        return "systemd" if found else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def override_warnings(settings_path, projects, managed, environment):
    """Name every place outside the activated file that Claude Code reads first."""
    lines = []
    layers = [project / ".claude" / name for project in projects for name in ("settings.json", "settings.local.json")]
    for path in layers + ([managed] if managed else []):
        if not path.exists() or path.resolve() == settings_path.resolve():
            continue
        try:
            data = astack_settings.json_loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lines.append("warn    {} does not load as JSON; check it for router keys by hand".format(shown(path)))
            continue
        for key in BEFORE_HELPER + CLIENT_KEYS:
            try:
                present = astack_settings.lookup(data, tuple(key.split(".")))["exists"]
            except ValueError:
                present = False
            if present and key in BEFORE_HELPER:
                lines.append("warn    {} sets {}, which Claude Code uses instead of the router's apiKeyHelper".format(shown(path), key))
            elif present:
                lines.append("warn    {} sets {}, which Claude Code uses instead of {}".format(shown(path), key, shown(settings_path)))
    for key in BEFORE_HELPER:
        name = key.split(".", 1)[1]
        if environment.get(name):
            lines.append("warn    this environment exports {}, which Claude Code uses instead of the router's apiKeyHelper".format(name))
        manager = launcher_has(name)
        if manager:
            lines.append("warn    {} gives the apps it starts {}, which Claude Code uses instead of the router's apiKeyHelper".format(manager, name))
    return lines


def read_journal(state_dir):
    path = state_dir / ACTIVATION
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_journal(state_dir, journal):
    files.atomic_write(state_dir / ACTIVATION, (json.dumps(journal, indent=2) + "\n").encode(), 0o600)


def undo_interrupted(state_dir, state, manifest_path):
    """Restore the settings file an interrupted activation wrote, and drop its ownership record."""
    journal = read_journal(state_dir)
    if not journal or journal["phase"] == "active":
        return None
    settings = Path(journal["settings"])
    current = file_sha256(settings)
    if current == journal["activated_sha256"]:
        if journal["original_sha256"] is None:
            settings.unlink()
        else:
            backup = Path(journal["backup"])
            if file_sha256(backup) != journal["original_sha256"]:
                raise Refusal("the backup of {} changed: {}".format(shown(settings), shown(backup)),
                              next_step="restore {} by hand, then remove {}".format(shown(settings), shown(state_dir / ACTIVATION)))
            files.atomic_write(settings, backup.read_bytes(), journal["mode"])
    elif current != journal["original_sha256"]:
        raise Refusal("{} changed after an interrupted activation".format(shown(settings)),
                      ["backup  {}".format(shown(Path(journal["backup"]))) if journal["backup"] else "before  the file did not exist"],
                      "restore the file or remove the router keys, then remove {} (local edits are kept)".format(shown(state_dir / ACTIVATION)))
    owned = state.get("settings", {}).get(NAME, {})
    if str(settings) in owned:
        del owned[str(settings)]
        if not owned:
            state["settings"].pop(NAME)
        files.save_manifest(manifest_path, state)
    (state_dir / ACTIVATION).unlink()
    return settings


def run_verify(command, timeout):
    process = subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return None


def routed_requests(events, offset):
    if not events.exists():
        return 0
    with open(events, "rb") as stream:
        stream.seek(offset)
        count = 0
        for line in stream.read().decode("utf-8", "replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            count += event.get("kind") == "request" and event.get("status") == 200 and event.get("outcome") == "completed"
    return count


def verify_wiring(lay, listen, helper, commands, timeout):
    """Return the failed check, or None when every check passed."""
    printed = subprocess.run(["/bin/sh", "-c", helper], capture_output=True, text=True, timeout=60)
    try:
        fetch_status(listen, printed.stdout.strip())
    except (OSError, ValueError):
        return "the router refuses what apiKeyHelper prints"
    print("verify  apiKeyHelper prints a token the router accepts")
    events = lay.state / "events.jsonl"
    for command in commands:
        offset = events.stat().st_size if events.exists() else 0
        code = run_verify(command, timeout)
        routed = routed_requests(events, offset)
        if code != 0:
            return "{} {}".format(command, "timed out after {} s".format(timeout) if code is None else "exited {}".format(code))
        if routed == 0:
            return "{} completed no request through the router".format(command)
        print("verify  {}: exit 0, {} routed request{}".format(command, routed, "" if routed == 1 else "s"))
    return None


def activate(args, state_root, home, use_env):
    manifest_path, state = load_state(state_root)
    lay, record = installed_layout(state, state_root, home)
    journal = read_journal(lay.state)
    if journal and journal["phase"] != "active":
        if args.prepare:
            print("undo    an interrupted activation of {}; activate restores it first".format(shown(Path(journal["settings"]))))
            print("ready   nothing changed; activate without --prepare")
            return 0
        print("undo    {} restored after an interrupted activation".format(shown(undo_interrupted(lay.state, state, manifest_path))))
    owned = sorted(state.get("settings", {}).get(NAME, {}))
    if owned:
        raise Refusal("router activation already owns {}".format(", ".join(shown(Path(name)) for name in owned)),
                      next_step="claude-router direct, then run again")
    try:
        accepted = json.loads(args.acceptance.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal("cannot read the acceptance record {}: {}".format(args.acceptance, error),
                      next_step="run the acceptance matrix and pass its record with --acceptance") from None
    listen, status = running_status(lay.state)
    current = measure(lay, record, status, args.claude, args.agent_sdk)
    found = mismatches(accepted if isinstance(accepted, dict) else {}, current)
    if found:
        raise Refusal("the acceptance record does not match this installation", found,
                      "run the acceptance matrix at this installation, or install the accepted head")
    usable, problems = usable_accounts(status)
    if problems:
        raise Refusal("the router is not ready", problems, "claude-router status")
    settings_path = args.settings or (Path(os.environ["CLAUDE_CONFIG_DIR"]) if use_env and os.environ.get("CLAUDE_CONFIG_DIR")
                                      else home / ".claude") / "settings.json"
    settings_path = settings_path.absolute()
    text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
    try:
        values, replaced = client_settings(lay.state, astack_settings.document("json", text).data)
    except ValueError as error:
        raise Refusal(str(error), next_step="remove the key from {}, then run again".format(shown(settings_path))) from None
    change, = astack_settings.prepare(NAME, {}, {str(settings_path): ("json", values)})
    print("accept  head {}, binary {}, sdk {}, claude {}, agent sdk {}".format(
        current["head"][:7], short(current["binary_sha256"]), current["sdk"],
        current["clients"]["claude_code"], current["clients"]["agent_sdk"]))
    print("ready   router running {}; usable accounts {}".format(listen, ", ".join(usable)))
    before = astack_settings.document("json", text)
    for key in CLIENT_KEYS:
        path = tuple(key.split("."))
        installed_value = astack_settings.lookup(values, path)
        if key in replaced:
            print("replace {} {} ({} with {})".format(shown(settings_path), key, redact(key, before.get(path)), redact(key, installed_value)))
        else:
            print("set     {} {} ({})".format(shown(settings_path), key, redact(key, installed_value)))
    for line in override_warnings(settings_path, [p.absolute() for p in args.project],
                                  MANAGED_SETTINGS.get(sys.platform), os.environ):
        print(line)
    original = settings_path.read_bytes() if settings_path.exists() else None
    backup = (manifest_path.parent / "backups" / ("activate-" + hashlib.sha256(original).hexdigest()[:12]) / settings_path.name
              if original is not None else None)
    if backup:
        print("backup  {}".format(shown(backup)))
    print("undo    claude-router direct")
    if args.prepare:
        print("ready   nothing changed; activate without --prepare")
        return 0
    if not args.verify:
        raise Refusal("activation needs at least one --verify continuation", next_step="pass --verify for the terminal client and for T3")
    if backup:
        files.atomic_write(backup, original, 0o600)
    journal = {"phase": "writing", "settings": str(settings_path), "mode": stat.S_IMODE(settings_path.stat().st_mode) if original is not None else 0o600,
               "original_sha256": hashlib.sha256(original).hexdigest() if original is not None else None,
               "activated_sha256": hashlib.sha256(change.rendered.encode()).hexdigest(),
               "backup": str(backup) if backup else None, "undo": "claude-router direct",
               "acceptance": {key: accepted.get(key) for key in ("head", "binary_sha256", "sdk", "clients")},
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_journal(lay.state, journal)
    with redirect_stdout(io.StringIO()):
        change.apply(state, manifest_path, manifest_path.parent / "backups" / uuid.uuid4().hex)
    write_journal(lay.state, dict(journal, phase="verifying"))
    failed = verify_wiring(lay, listen, values["apiKeyHelper"], args.verify, args.verify_timeout)
    if failed:
        undo_interrupted(lay.state, state, manifest_path)
        print("claude-router: activate: {}".format(failed), file=sys.stderr)
        print("undo    {} restored; the router keeps its logins and assignments".format(shown(settings_path)), file=sys.stderr)
        print("next    claude-router status", file=sys.stderr)
        return 1
    write_journal(lay.state, dict(journal, phase="active", activated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    print("done    clients connect through the router; undo with claude-router direct")
    return 0


def print_identity(args, state_root, home):
    _, state = load_state(state_root)
    lay, record = installed_layout(state, state_root, home)
    _, status = running_status(lay.state)
    print(json.dumps(measure(lay, record, status, args.claude, args.agent_sdk), indent=2))
    return 0


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
    a = sub.add_parser("activate", help="point Claude Code at the router after acceptance passes")
    i = sub.add_parser("identity", help="print the installation's identity for an acceptance record")
    for p in (a, i):
        p.add_argument("--home", type=Path, help="alternate home; ignores configuration environment overrides")
        p.add_argument("--claude", type=Path, required=True, help="the terminal Claude Code executable to check")
        p.add_argument("--agent-sdk", type=Path, required=True, help="the installed @anthropic-ai/claude-agent-sdk package directory")
    a.add_argument("--acceptance", type=Path, required=True, help="the acceptance record of this installation")
    a.add_argument("--settings", type=Path, help="Claude settings file; default $CLAUDE_CONFIG_DIR or ~/.claude, then settings.json")
    a.add_argument("--project", type=Path, action="append", default=[], help="a project whose .claude settings to check; repeat")
    a.add_argument("--verify", action="append", default=[], help="a shell command that continues a conversation; repeat per client")
    a.add_argument("--verify-timeout", type=int, default=600, help="seconds each --verify command may take")
    a.add_argument("--prepare", action="store_true", help="show the changes without making them")
    args = parser.parse_args()
    if args.command == "direct":
        return direct(args.state, args.manifest, "prepare" if args.prepare else "check" if args.check else "apply", args.stopped)
    home = (args.home or Path.home()).expanduser().resolve()
    use_env = args.home is None
    state_root = env_dir("XDG_STATE_HOME", ".local/state", home, use_env) / "astack"
    if args.command in ("activate", "identity"):
        try:
            return activate(args, state_root, home, use_env) if args.command == "activate" else print_identity(args, state_root, home)
        except Refusal as refusal:
            report(args.command, refusal)
            return 1
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
