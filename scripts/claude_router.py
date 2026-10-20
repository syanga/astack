#!/usr/bin/env python3
"""Install, preview, update, and remove the optional claude-router component.

The component is a Go binary built from the pinned source, a per-user service
descriptor, and a state directory. Installing it never changes client settings.
"""

import argparse
from contextlib import nullcontext, redirect_stdout
import fcntl
from enum import Enum
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
MIN_TOKEN_LENGTH = 32
HELPER = ("claude_router.py", "astack_files.py", "astack_settings.py")
ACTIVATION = "activation.json"
ACCEPTED = "accepted.json"
MANAGED_SETTINGS = {"darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                    "linux": Path("/etc/claude-code/managed-settings.json")}


def repo():
    return SCRIPTS.parent


def component_dir():
    return repo() / "components" / NAME


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
    use_env: bool = True

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
                  manager=manager, service=service, descriptor=descriptor, port=port, use_env=use_env)


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
    """Return the Go command and its version after checking the version and target platform."""
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
    return go, version


def source_revision():
    head = subprocess.run(["git", "-C", str(component_dir()), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True)
    return head.stdout.strip() if head.returncode == 0 else "unknown"


def router_code(relative):
    parts = relative.split("/")
    return not (relative.endswith((".md", "_test.go")) or "testdata" in parts or relative == ACCEPTED)


def git(*arguments, data=None):
    result = subprocess.run(["git", "-C", str(component_dir()), *arguments], input=data, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def committed_code():
    tree = git("ls-tree", "-r", "-z", "HEAD", "--", ".")
    if tree is None:
        return None
    entries = []
    for entry in tree.decode().split("\0"):
        if entry:
            meta, relative = entry.split("\t", 1)
            _, kind, obj = meta.split()
            if kind == "blob" and router_code(relative):
                entries.append((obj, relative))
    batch = git("cat-file", "--batch", data="".join(obj + "\n" for obj, _ in entries).encode())
    contents, offset = {}, 0
    for _, relative in entries:
        header_end = batch.index(b"\n", offset)
        size = int(batch[offset:header_end].split()[2])
        contents[relative] = batch[header_end + 1:header_end + 1 + size]
        offset = header_end + 2 + size
    return contents


def working_code():
    root = component_dir()
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and router_code(path.relative_to(root).as_posix())
            and not any(part.startswith(".") for part in path.relative_to(root).parts)}


def code_sha256():
    contents = committed_code()
    return payload_digest(working_code() if contents is None else contents)


def check_code_committed():
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    prefix = git("rev-parse", "--show-prefix")
    if status is None or prefix is None:
        return
    prefix = prefix.decode().strip()
    changed = []
    records = iter(status.decode().split("\0"))
    for record in records:
        if not record:
            continue
        if record[0] in "RC":
            next(records, None)
        relative = record[3:][len(prefix):] if record[3:].startswith(prefix) else record[3:]
        if router_code(relative):
            changed.append(relative)
    if changed:
        raise ValueError("claude-router code differs from the checkout's commit: {}; commit or revert it, then install again".format(
            ", ".join(sorted(changed))))


def build(go, directory, code):
    """Build the binary from the pinned dependency cache into directory."""
    output = directory / NAME
    env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOFLAGS="-mod=readonly", GOWORK="off", CGO_ENABLED="0")
    package = "github.com/syanga/astack/components/claude-router/internal/claude."
    flags = "-X {}linkedRevision={} -X {}linkedCodeDigest={}".format(package, source_revision(), package, code)
    result = subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-ldflags=" + flags, "-o", str(output), "./cmd/claude-router"],
                            cwd=str(component_dir()), env=env, capture_output=True, text=True)
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
    record_path = component_dir() / ACCEPTED
    if record_path.is_file():
        result[str(lay.data / ACCEPTED)] = (record_path.read_bytes(), 0o644)
    if lay.descriptor:
        result[str(lay.descriptor)] = (descriptor_text(lay).encode(), 0o644)
    return result


def seed_files(lay):
    """Files created once and then owned by the user: the router configuration."""
    config = {"listen": "127.0.0.1:{}".format(lay.port), "state_dir": str(lay.state),
              "client_token_file": str(lay.token), "accounts": []}
    return {str(lay.config): ((json.dumps(config, indent=2) + "\n").encode(), 0o600)}


def token_usable(path):
    return len(path.read_bytes().strip()) >= MIN_TOKEN_LENGTH


def publish_token(path):
    data = (secrets.token_hex(32) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".client-token-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    directory = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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


class ActivationLock:
    def __init__(self, state_dir, fd=None):
        self.opened = fd is None
        if fd is None:
            try:
                fd = os.open(str(state_dir / "activation.lock"), os.O_RDWR | os.O_CREAT, 0o600)
            except FileNotFoundError:
                raise Refusal("no state directory at {}".format(shown(state_dir))) from None
        self.fd = fd
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.__exit__()
            raise Refusal("another claude-router setup, account, activate, direct, install, or uninstall is running",
                          next_step="wait for it to finish, then run again") from None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def close(self):
        if self.opened:
            os.close(self.fd)
            self.opened = False


class ShellAction(Enum):
    ADD = "add"
    KEEP = "keep"
    ALREADY_ON_PATH = "already on PATH"
    DECLINED_EARLIER = "declined earlier"
    DECLINED = "declined"
    UNKNOWN_SHELL = "unknown shell"
    REMOVE = "remove"


@dataclass(frozen=True)
class ShellChange:
    action: ShellAction
    file: Path | None
    lines: list


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
    shell: ShellChange | None = None
    code_sha256: str | None = None
    go: str | None = None
    lock: ActivationLock | None = None

    def paths(self):
        return [path for path, *_ in self.operations] + [path for path, *_ in self.seeds]


def plan(command, state, lay, force, lock):
    """Compute the component's changes without writing anything."""
    old = state.get("components", {}).get(NAME, {})
    if command == "install" and old.get("layout") and old["layout"] != lay.record():
        raise ValueError("{} destination changed; uninstall the component before reinstalling".format(NAME))
    if old.get("layout"):
        recorded = old["layout"]
        lay = Layout(home=lay.home, data=Path(recorded["data"]), state=Path(recorded["state"]), manifest=lay.manifest,
                     manager=recorded["manager"], service=recorded["service"],
                     descriptor=Path(recorded["descriptor"]) if recorded["descriptor"] else None, port=lay.port,
                     use_env=lay.use_env)
    return planned(Plan(command=command, lay=lay, old=old, lock=lock), state, force)


def planned(result, state, force):
    command, lay, old = result.command, result.lay, result.old
    journal = read_journal(lay.state) if command == "uninstall" and old else None
    if journal and journal["phase"] != "active":
        raise ValueError("An activation of claude-router was interrupted; run claude-router direct, then uninstall")
    if journal and not state.get("settings", {}).get(NAME):
        raise ValueError("An activation of claude-router has no settings record; run claude-router direct, then uninstall")
    if command == "uninstall" and old and (journal or state.get("settings", {}).get(NAME)):
        try:
            check_checkout(lay)
        except Refusal as refusal:
            raise ValueError("Uninstall would restore client settings, but {} ({}); run claude-router direct, then uninstall".format(
                refusal, refusal.details[0])) from None
    desired, seeds = {}, {}
    if command == "install":
        if lay.token.exists() and not lay.token.is_file():
            raise ValueError("{} is not a file; move it aside, then install again".format(lay.token))
        go, result.go = check_go()
        check_code_committed()
        result.code_sha256 = code_sha256()
        with tempfile.TemporaryDirectory(prefix="astack-claude-router-") as directory:
            binary = build(go, Path(directory), result.code_sha256)
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
    result.shell = shell_change(command, lay, old)
    if command == "uninstall":
        previous = state.get("settings", {}).get(NAME, {})
        result.settings = astack_settings.prepare(NAME, previous, {}, force)
        result.conflicts.extend("{} [{}]".format(change.path, key) for change in result.settings for key in change.conflicts)
    return result


PATH_MARKER = "# astack: claude-router on PATH"


def shell_file_for(home, use_env):
    shell = Path(os.environ.get("SHELL", "")).name or ("zsh" if sys.platform == "darwin" else "bash")
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR") if use_env else None
        return Path(zdotdir or home) / ".zshrc"
    if shell != "bash":
        return None
    if sys.platform != "darwin":
        return home / ".bashrc"
    # Terminal on macOS starts bash as a login shell, which reads the first of these that exists.
    login = [home / name for name in (".bash_profile", ".bash_login", ".profile")]
    return next((path for path in login if path.is_file()), login[0])


def shell_change(command, lay, old):
    owned = old.get("shell_path")
    if command == "uninstall":
        return ShellChange(ShellAction.REMOVE, Path(owned["file"]), owned["lines"]) if owned else None
    directory = re.sub(r'([\\"$`])', r"\\\1", str(lay.binary.parent))
    lines = [PATH_MARKER, 'export PATH="{}:$PATH"'.format(directory)]
    shell_file = Path(owned["file"]) if owned else shell_file_for(lay.home, lay.use_env)
    if shell_file is None:
        return ShellChange(ShellAction.UNKNOWN_SHELL, None, lines)
    text = shell_file.read_text() if shell_file.is_file() else ""
    if owned and "\n".join(lines) + "\n" in text:
        return ShellChange(ShellAction.KEEP, shell_file, lines)
    if old.get("shell_path_declined"):
        return ShellChange(ShellAction.DECLINED_EARLIER, shell_file, lines)
    active = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    if any("astack/claude-router/bin" in line or str(lay.binary.parent) in line for line in active):
        return ShellChange(ShellAction.ALREADY_ON_PATH, shell_file, lines)
    return ShellChange(ShellAction.ADD, shell_file, lines)


def on_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_shell(change):
    if change.action is not ShellAction.ADD or not on_terminal():
        return change
    try:
        answer = input("Add claude-router to PATH in {}? [Y/n] ".format(change.file))
    except EOFError:
        answer = ""
    if answer.strip().lower() in ("n", "no"):
        return ShellChange(ShellAction.DECLINED, change.file, change.lines)
    return change


def apply_shell(change, record, dry_run):
    shell_file, line = change.file, change.lines[1]
    block = "\n".join(change.lines) + "\n"
    action = change.action
    if action is ShellAction.KEEP:
        print("KEEP {}".format(shell_file))
    elif action is ShellAction.ALREADY_ON_PATH:
        print("KEEP {} (already puts claude-router on PATH)".format(shell_file))
        record.pop("shell_path", None)
    elif action is ShellAction.UNKNOWN_SHELL:
        print("SKIP PATH (no shell file known for SHELL={}; to add it yourself: {})".format(os.environ.get("SHELL", ""), line))
    elif action is ShellAction.DECLINED_EARLIER:
        print("SKIP {} (declined at an earlier install; to add it yourself: {})".format(shell_file, line))
    elif action is ShellAction.DECLINED:
        print("SKIP {} (to add it yourself: {})".format(shell_file, line))
        record.pop("shell_path", None)
        record["shell_path_declined"] = True
    elif dry_run:
        print("{} {} {}".format("APPEND" if action is ShellAction.ADD else "REMOVE", shell_file, line))
    else:
        target = shell_file.resolve() if shell_file.is_symlink() else shell_file
        existed = target.is_file()
        text = target.read_text() if existed else ""
        mode = stat.S_IMODE(target.stat().st_mode) if existed else 0o644
        if action is ShellAction.REMOVE:
            if block in text:
                text = text.replace(block, "", 1)
                if text or not record.get("shell_path", {}).get("created"):
                    files.atomic_write(target, text.encode(), mode)
                else:
                    target.unlink()
                print("REMOVE {} {}".format(shell_file, line))
            record.pop("shell_path", None)
            return
        if text and not text.endswith("\n"):
            text += "\n"
        files.atomic_write(target, (text + block).encode(), mode)
        record["shell_path"] = {"file": str(shell_file), "lines": change.lines, "created": not existed}
        print("APPEND {} {}".format(shell_file, line))


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
    if p.shell and not dry_run:
        p.shell = ask_shell(p.shell)
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
        for stray in sorted(lay.state.glob(".client-token-*")):
            print("REMOVE {}".format(stray))
            if not dry_run:
                stray.unlink()
        if not lay.token.exists():
            print("CREATE {}".format(lay.token))
            if not dry_run:
                publish_token(lay.token)
        elif not token_usable(lay.token):
            print("REPLACE {} (fewer than {} characters)".format(lay.token, MIN_TOKEN_LENGTH))
            if not dry_run:
                publish_token(lay.token)
        elif (mode := stat.S_IMODE(lay.token.stat().st_mode)) != 0o600:
            print("CHMOD {} 0600 (was {:04o})".format(lay.token, mode))
            if not dry_run:
                lay.token.chmod(0o600)
    activated = p.command == "install" and is_activated(lay.state, state)
    if p.command == "install" and p.binary_changed and router_running(lay) and not activated:
        print("RESTART needed to run the new binary: claude-router stop; claude-router start")
    if p.shell:
        apply_shell(p.shell, record, dry_run)
        save()
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
        record["code_sha256"] = p.code_sha256
        record["go"] = p.go
    save()
    if activated and any(action == "write" for _, action, *_ in p.operations):
        if p.binary_changed and lay.manager != "foreground":
            print("RESTART {} {}".format(lay.manager, lay.service))
        print("CHECK claude-router check; claude-router direct if it fails")
        if not dry_run:
            recheck(p, state, state_path)


def is_activated(state_dir, state):
    journal = read_journal(state_dir) if state_dir.is_dir() else None
    return bool(journal and journal["phase"] == "active" and state.get("settings", {}).get(NAME))


def service_command(lay, *arguments):
    result = subprocess.run([str(lay.binary), *arguments, "-state", str(lay.state)], capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError("claude-router {} failed: {}".format(arguments[0], (result.stderr or result.stdout).strip()))


def recheck(p, state, state_path):
    lay = p.lay
    stop = Stop()
    stop.install()
    try:
        failure = recheck_failure(p, state, stop)
        if failure is None:
            return
        stopped = False
        if lay.manager != "foreground":
            try:
                service_command(lay, "stop")
                stopped = True
            except (OSError, ValueError) as error:
                say("claude-router: {}".format(error), sys.stderr)
        if direct(lay.state, state_path, "apply", stopped=stopped, lock_fd=p.lock.fd if p.lock else None) != 0:
            raise ValueError(failure + ", and claude-router direct did not finish; run claude-router direct")
        say(CHECK_LINE.format("next", "claude-router check"))
        raise ValueError(failure + "; Claude Code connects directly")
    finally:
        stop.uninstall()


def recheck_failure(p, state, stop):
    lay = p.lay
    try:
        if p.binary_changed and lay.manager != "foreground":
            service_command(lay, "stop")
            service_command(lay, "start")
        journal = read_journal(lay.state)
        record_path = lay.data / ACCEPTED
        result = run_check(lay, state, Path(journal["settings"]), read_record(record_path), record_path, "claude", CHECK_TIMEOUT, stop)
    except (OSError, ValueError, KeyError) as error:
        return "the update could not check the router ({})".format(error)
    if result is None:
        return None
    return "the update was interrupted" if result == INTERRUPTED else "the updated router failed its check"


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


def direct(state_dir, manifest_path, mode, stopped=False, lock_fd=None):
    try:
        with ActivationLock(state_dir, lock_fd):
            return restore_direct(state_dir, manifest_path, mode, stopped)
    except Refusal as refusal:
        report("direct", refusal)
        return 1


def restore_direct(state_dir, manifest_path, mode, stopped):
    state = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    owned = state.get("settings", {}).get(NAME, {})
    inventory = router_inventory(state_dir)
    done = "done    clients connect directly; the router stays installed" + (" and stopped" if stopped else "")
    journal = read_journal(state_dir)
    if journal and (journal["phase"] != "active" or not owned):
        reason = "interrupted activation" if journal["phase"] != "active" else "activation with no settings record"
        restores = file_sha256(Path(journal["settings"])) == journal["activated_sha256"]
        try:
            settings = undo_journal(state_dir, state, manifest_path, check=mode != "apply")
        except Refusal as refusal:
            report("direct", refusal)
            return 1
        if mode == "check":
            return 0
        print("restore {} ({})".format(shown(settings), reason) if restores
              else "skip    {} (already the original; {})".format(shown(settings), reason))
        print("keep    " + inventory)
        print("ready   nothing changed; apply with claude-router direct" if mode == "prepare" else done)
        return 0
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
    def __init__(self, error, details=(), next_step=None):
        super().__init__(error)
        self.details = list(details)
        self.next_step = next_step


def say(line, stream=None):
    stream = stream or sys.stdout
    try:
        print(line, file=stream, flush=True)
    except OSError:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, stream.fileno())
        os.close(null)


def report(command, refusal, width=8):
    say("claude-router: {}: {}".format(command, refusal), sys.stderr)
    for line in refusal.details:
        say(" " * width + line, sys.stderr)
    if refusal.next_step:
        say("next".ljust(width) + refusal.next_step, sys.stderr)


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


def payload_digest(contents):
    digest = hashlib.sha256()
    for relative in sorted(contents):
        digest.update("{}\0{}\n".format(relative, hashlib.sha256(contents[relative]).hexdigest()).encode())
    return digest.hexdigest()


def payload_sha256(lib):
    if not lib.is_dir():
        return None
    return payload_digest({path.relative_to(lib).as_posix(): path.read_bytes() for path in lib.rglob("*")
                           if path.is_file() and "__pycache__" not in path.relative_to(lib).parts
                           and path.suffix not in (".pyc", ".pyo")})


def check_checkout(lay):
    checkout = payload_digest({relative: payload for relative, (payload, _) in helper_files().items()})
    installed = payload_sha256(lay.lib)
    if checkout != installed:
        raise Refusal("the helpers running this command differ from the installed ones",
                      ["helpers: running {} from {}, installed {}".format(short(checkout), shown(SCRIPTS), short(installed))],
                      "reinstall from a checkout of the accepted head: ./install.sh --component claude-router"
                      if installed is None or SCRIPTS == lay.lib.resolve() else "run {} instead".format(shown(lay.lib / "claude_router.py")))


def measure(lay, record, status):
    return {"head": record.get("commit"), "code_sha256": record.get("code_sha256"), "binary_sha256": file_sha256(lay.binary),
            "helpers_sha256": payload_sha256(lay.lib), "running_binary": status.get("binary"),
            "running_code_sha256": status.get("code_sha256"), "running_binary_sha256": status.get("binary_sha256"),
            "sdk": status.get("sdk"), "go": record.get("go"), "platform": "{}/{}".format(*host_platform())}


def short(value):
    return value[:12] if isinstance(value, str) else "none"


def mismatches(accepted, current):
    found = []
    if accepted.get("result") != "pass":
        found.append("result: {}, not pass".format(accepted.get("result", "missing")))
    code = accepted.get("code_sha256")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9a-f]{64}", code):
        found.append("code: the record names no digest")
    elif code != current["code_sha256"]:
        found.append("code: accepted {}, installed {}".format(short(code), short(current["code_sha256"])))
    elif code != current["running_code_sha256"]:
        found.append("code: accepted {}, running {}".format(short(code), short(current["running_code_sha256"])))
    if current["binary_sha256"] != current["running_binary_sha256"]:
        found.append("binary: installed {}, running {}".format(short(current["binary_sha256"]), short(current["running_binary_sha256"])))
    helpers = accepted.get("helpers_sha256")
    if not isinstance(helpers, str) or not re.fullmatch(r"[0-9a-f]{64}", helpers):
        found.append("helpers: the record names no digest")
    elif helpers != current["helpers_sha256"]:
        found.append("helpers: accepted {}, installed {}".format(short(helpers), short(current["helpers_sha256"])))
    if accepted.get("sdk") != current["sdk"]:
        found.append("sdk: accepted {}, running {}".format(accepted.get("sdk"), current["sdk"]))
    return found


def platform_note(accepted, current):
    recorded = accepted.get("platform")
    if not isinstance(recorded, str) or recorded == current["platform"]:
        return None
    untested = ", not yet tested on real Linux" if current["platform"].startswith("linux/") else ""
    return "accepted on {}, this is {}{}".format(recorded, current["platform"], untested)


CHECK_LINE = "{:<9} {}"
CHECK_TIMEOUT = 120
CHECK_PROMPT = "ping"


def joined(names):
    names = list(names)
    if len(names) < 3:
        return " and ".join(names)
    return "{}, and {}".format(", ".join(names[:-1]), names[-1])


def read_record(path):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def run_check(lay, state, settings_path, accepted, record_path, claude, timeout, stop, passed_line=True):
    steps = []

    def line(result, text, details=(), next_step=None):
        say(CHECK_LINE.format(result, text))
        for detail in details:
            say(CHECK_LINE.format("", detail))
        if result == "fail":
            steps.append(next_step)

    config = json.loads(lay.config.read_text(encoding="utf-8"))
    listen = config["listen"]
    try:
        status = fetch_status(listen, (Path(lay.state).absolute() / config["client_token_file"]).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        status = None
    if status is None:
        line("fail", "router not answering on {}".format(listen), next_step="claude-router start; claude-router check")
    else:
        line("ok", "router running on {} ({})".format(listen, lay.manager))
    names = [account["id"] for account in config.get("accounts") or []]
    seen = {account.get("id"): account for account in (status or {}).get("accounts") or []}
    stopped = "not read while the router is stopped"
    if status is None:
        line("skip", "accounts, " + stopped)
        line("skip", "paid overflow, " + stopped)
    elif not names:
        line("fail", "no accounts in config.json", next_step="claude-router setup")
        line("skip", "paid overflow, no accounts to read")
    else:
        unready = [name for name in names if not seen.get(name, {}).get("registered") or seen[name].get("needs_login")]
        if unready:
            line("fail", "{} not signed in".format(joined(unready)), next_step="claude-router status")
        else:
            line("ok", "{} ready".format(", ".join(names)))
        readings = {name: (seen.get(name, {}).get("overage") or {}).get("state") for name in names}
        enabled = [name for name in names if readings[name] in ("enabled", "paid_use")]
        unknown = [name for name in names if readings[name] not in ("enabled", "paid_use", "disabled")]
        if enabled:
            who = enabled[0] if len(enabled) == 1 else "their"
            line("fail", "paid overflow on for {}".format(joined(enabled)),
                 next_step="turn off {} paid overflow on claude.ai; claude-router check".format(who))
        elif unknown:
            line("fail", "paid overflow not read yet for {}".format(joined(unknown)), next_step="claude-router status")
        else:
            line("ok", "paid overflow off on {}".format(
                names[0] if len(names) == 1 else "both accounts" if len(names) == 2 else "all {} accounts".format(len(names))))
    if points_at_router(lay.state, settings_path):
        line("ok", "Claude Code settings point at the router")
    else:
        line("fail", "Claude Code settings do not point at the router", next_step="claude-router setup")
    if accepted is None:
        line("fail", "no accepted record at {}".format(shown(record_path)),
             next_step="install a checkout whose accepted.json matches its code")
    elif status is None:
        line("skip", "accepted record, not compared while the router is stopped")
    else:
        found = mismatches(accepted, measure(lay, state.get("components", {}).get(NAME, {}), status))
        where = " ({})".format(accepted["platform"]) if isinstance(accepted.get("platform"), str) else ""
        if found:
            line("fail", "code differs from the accepted record" + where, found,
                 "install a checkout whose accepted.json matches its code")
        else:
            line("ok", "code matches the accepted record" + where)
    if stop.signal:
        return INTERRUPTED
    if steps:
        line("skip", "test message, not sent while a check fails")
    else:
        sent = test_message(lay, claude, timeout, stop)
        if sent is None:
            return INTERRUPTED
        code, elapsed, account = sent
        if account:
            line("ok", "test message answered 200 on {} in {:.1f}s".format(account, elapsed))
        else:
            reason = "timed out after {} s".format(timeout) if code is None else "claude exited {}".format(code)
            line("fail", "test message got no answer through the router ({})".format(reason), next_step="claude-router status")
    if steps or passed_line:
        say(CHECK_LINE.format("check", "failed" if steps else "passed"))
    return steps[0] if steps else None


def points_at_router(state_dir, settings_path):
    text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
    try:
        current = astack_settings.document("json", text).data
        values, _ = client_settings(state_dir, current)
    except ValueError:
        return False
    return all(astack_settings.same(astack_settings.lookup(current, path), astack_settings.lookup(values, path))
               for path in (tuple(key.split(".")) for key in CLIENT_KEYS))


def test_message(lay, claude, timeout, stop):
    session = str(uuid.uuid4())
    events = lay.state / "events.jsonl"
    offset = events.stat().st_size if events.exists() else 0
    command = [claude, "-p", CHECK_PROMPT, "--session-id", session, "--strict-mcp-config", "--no-session-persistence", "--tools", ""]
    started = time.monotonic()
    code = run_verify(" ".join(shlex.quote(word) for word in command) + " < /dev/null > /dev/null 2>&1", timeout, stop)
    elapsed = time.monotonic() - started
    if stop.signal:
        return None
    served = routed_requests(events, offset, conversation_hash(session))
    return code, elapsed, served[-1] if served else None


def usable_accounts(status):
    problems = [] if status.get("journal") == "ok" else ["assignment journal: {}".format(status.get("journal"))]
    usable = [a["id"] for a in status.get("accounts") or []
              if a.get("registered") and not a.get("needs_login") and (a.get("overage") or {}).get("state") == "disabled"]
    if not usable:
        problems.append("no account has a login and a reading that paid overflow is disabled")
    return usable, problems


def launcher_has(name):
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


def hold_activation(state_dir):
    try:
        return ActivationLock(state_dir)
    except Refusal as refusal:
        raise ValueError("; ".join(filter(None, (str(refusal), refusal.next_step)))) from None


def recorded_state_dir(state):
    recorded = state.get("components", {}).get(NAME, {}).get("layout")
    return Path(recorded["state"]) if recorded and Path(recorded["state"]).is_dir() else None


def locked_state(state_root, dry_run):
    state_path, state = load_state(state_root)
    state_dir = None if dry_run else recorded_state_dir(state)
    if state_dir is None:
        return state_path, state, None
    lock = hold_activation(state_dir)
    try:
        state_path, state = load_state(state_root)
        if recorded_state_dir(state) != state_dir:
            raise ValueError("The claude-router installation changed before this command took its lock; run it again")
    except BaseException:
        lock.close()
        raise
    return state_path, state, lock


def undo_journal(state_dir, state, manifest_path, check=False):
    journal = read_journal(state_dir)
    settings = Path(journal["settings"])
    current = file_sha256(settings)
    if current not in (journal["activated_sha256"], journal["original_sha256"]):
        when = "activation" if journal["phase"] == "active" else "an interrupted activation"
        raise Refusal("{} changed after {}".format(shown(settings), when),
                      ["backup  {}".format(shown(Path(journal["backup"]))) if journal["backup"] else "before  the file did not exist"],
                      "restore the file or remove the router keys, then remove {} (local edits are kept)".format(shown(state_dir / ACTIVATION)))
    original = None
    if current == journal["activated_sha256"] and journal["original_sha256"] is not None:
        backup = Path(journal["backup"])
        original = backup.read_bytes() if backup.exists() else None
        if original is None or hashlib.sha256(original).hexdigest() != journal["original_sha256"]:
            raise Refusal("the backup of {} changed: {}".format(shown(settings), shown(backup)),
                          next_step="restore {} by hand, then remove {}".format(shown(settings), shown(state_dir / ACTIVATION)))
    if check:
        return settings
    if current == journal["activated_sha256"]:
        if original is None:
            settings.unlink()
        else:
            files.atomic_write(settings, original, journal["mode"])
    owned = state.get("settings", {}).get(NAME, {})
    if str(settings) in owned:
        del owned[str(settings)]
        if not owned:
            state["settings"].pop(NAME)
        files.save_manifest(manifest_path, state)
    (state_dir / ACTIVATION).unlink()
    return settings


def run_verify(command, timeout, stop):
    process = subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True)
    stop.group = process.pid
    stop.kill()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        stop.group = None
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def routed_requests(events, offset, conversation):
    if not events.exists():
        return []
    served = []
    with open(events, "rb") as stream:
        stream.seek(offset)
        for line in stream.read().decode("utf-8", "replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (isinstance(event, dict) and event.get("kind") == "request" and event.get("conversation") == conversation
                    and event.get("status") == 200 and event.get("outcome") == "completed"):
                served.append(event.get("account") or "an account")
    return served


def conversation_hash(session):
    return hashlib.sha256(session.strip().encode()).hexdigest()[:12]


def program(command):
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    words = [word for word in words if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", word)]
    return os.path.basename(words[0]) if words else "command"


def verify_wiring(lay, checks, timeout, stop):
    events = lay.state / "events.jsonl"
    for session, command in checks:
        if stop.signal:
            return INTERRUPTED
        conversation = conversation_hash(session)
        label = "{} (conversation {})".format(program(command), conversation)
        offset = events.stat().st_size if events.exists() else 0
        code = run_verify(command, timeout, stop)
        routed = len(routed_requests(events, offset, conversation))
        if stop.signal:
            return INTERRUPTED
        if code != 0:
            return "{} {}".format(label, "timed out after {} s".format(timeout) if code is None else "exited {}".format(code))
        if routed == 0:
            return "{} completed no request through the router".format(label)
        say("verify  {}: exit 0, {} routed request{}".format(label, routed, "" if routed == 1 else "s"))
    return None


STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
INTERRUPTED = "verification was interrupted"
CHECK_FAILED = "the check failed"


class Stop:
    def __init__(self):
        self.signal = None
        self.group = None

    def __call__(self, signum, frame):
        signal.pthread_sigmask(signal.SIG_BLOCK, STOPS)
        self.signal = self.signal or signum
        self.kill()

    def kill(self):
        if self.signal and self.group:
            try:
                os.killpg(self.group, signal.SIGKILL)
            except OSError:
                pass

    def install(self):
        self.saved = {signum: signal.getsignal(signum) for signum in STOPS}
        for signum, handler in self.saved.items():
            if handler is not signal.SIG_IGN:
                signal.signal(signum, self)

    def uninstall(self):
        for signum, handler in self.saved.items():
            signal.signal(signum, handler)


def activate(args, state_root, home, use_env):
    _, state = load_state(state_root)
    lay, _ = installed_layout(state, state_root, home)
    with ActivationLock(lay.state, args.lock_fd):
        return activate_locked(args, state_root, home, use_env)


def activate_locked(args, state_root, home, use_env):
    manifest_path, state = load_state(state_root)
    lay, record = installed_layout(state, state_root, home)
    check_checkout(lay)
    journal = read_journal(lay.state)
    if journal and journal["phase"] != "active":
        restores = file_sha256(Path(journal["settings"])) == journal["activated_sha256"]
        settings = undo_journal(lay.state, state, manifest_path, check=args.prepare)
        if not restores:
            print("skip    {} (already the original; interrupted activation)".format(shown(settings)))
        elif args.prepare:
            print("undo    an interrupted activation of {}; activate restores it first".format(shown(settings)))
        else:
            print("undo    {} restored after an interrupted activation".format(shown(settings)))
        if args.prepare:
            print("ready   nothing changed; activate without --prepare")
            return 0
    owned = sorted(state.get("settings", {}).get(NAME, {}))
    if owned:
        raise Refusal("router activation already owns {}".format(", ".join(shown(Path(name)) for name in owned)),
                      next_step="claude-router direct, then run again")
    acceptance = args.acceptance or lay.data / ACCEPTED
    try:
        accepted = json.loads(acceptance.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal("cannot read the acceptance record {}: {}".format(acceptance, error),
                      next_step="run the acceptance matrix and pass its record with --acceptance") from None
    listen, status = running_status(lay.state)
    current = measure(lay, record, status)
    found = mismatches(accepted if isinstance(accepted, dict) else {}, current)
    if found:
        raise Refusal("the acceptance record does not match this installation", found,
                      "run the acceptance matrix at this installation, or install the accepted head")
    usable, problems = usable_accounts(status)
    if problems:
        raise Refusal("the router is not ready", problems, "claude-router status")
    settings_path = (args.settings or default_settings(home, use_env)).absolute()
    text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
    try:
        values, replaced = client_settings(lay.state, astack_settings.document("json", text).data)
    except ValueError as error:
        raise Refusal(str(error), next_step="remove the key from {}, then run again".format(shown(settings_path))) from None
    change, = astack_settings.prepare(NAME, {}, {str(settings_path): ("json", values)})
    note = platform_note(accepted, current)
    show = (lambda line: None) if args.brief else print
    show("accept  code {}, helpers {}, sdk {}".format(short(current["code_sha256"]), short(current["helpers_sha256"]), current["sdk"]))
    if note:
        print(CHECK_LINE.format("note", note) if args.brief else "note    " + note)
    show("ready   router running {}; usable accounts {}".format(listen, ", ".join(usable)))
    original = settings_path.read_bytes() if settings_path.exists() else None
    backup = (manifest_path.parent / "backups" / ("activate-" + hashlib.sha256(original).hexdigest()[:12]) / settings_path.name
              if original is not None else None)
    before = astack_settings.document("json", text)
    for key in CLIENT_KEYS:
        path = tuple(key.split("."))
        installed_value = astack_settings.lookup(values, path)
        if key in replaced and SECRET_KEY.search(key.rsplit(".", 1)[-1]):
            show("replace {} {} (the old value stays in {})".format(shown(settings_path), key, shown(backup)))
        elif key in replaced:
            show("replace {} {} ({} with {})".format(shown(settings_path), key, redact(key, before.get(path)), redact(key, installed_value)))
        else:
            show("set     {} {} ({})".format(shown(settings_path), key, redact(key, installed_value)))
    for line in override_warnings(settings_path, [p.absolute() for p in args.project],
                                  MANAGED_SETTINGS.get(sys.platform), os.environ):
        print(line)
    if backup:
        show("backup  {}".format(shown(backup)))
    if not args.brief:
        say("undo    claude-router direct")
    if args.prepare:
        print("ready   nothing changed; activate without --prepare")
        return 0
    stop = Stop()
    stop.install()
    if backup:
        files.atomic_write(backup, original, 0o600)
    journal = {"phase": "writing", "settings": str(settings_path), "mode": stat.S_IMODE(settings_path.stat().st_mode) if original is not None else 0o600,
               "original_sha256": hashlib.sha256(original).hexdigest() if original is not None else None,
               "activated_sha256": hashlib.sha256(change.rendered.encode()).hexdigest(),
               "backup": str(backup) if backup else None, "undo": "claude-router direct",
               "acceptance": {key: accepted.get(key) for key in ("head", "code_sha256", "helpers_sha256", "sdk", "clients", "platform")},
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_journal(lay.state, journal)
    with redirect_stdout(io.StringIO()):
        change.apply(state, manifest_path, manifest_path.parent / "backups" / uuid.uuid4().hex)
    write_journal(lay.state, dict(journal, phase="verifying"))
    try:
        failed = verify_wiring(lay, args.verify, args.verify_timeout, stop) or (stop.signal and INTERRUPTED)
        if not failed and run_check(lay, state, settings_path, accepted, acceptance, str(args.claude), CHECK_TIMEOUT, stop,
                                    passed_line=not args.brief) is not None:
            failed = INTERRUPTED if stop.signal else CHECK_FAILED
    except (OSError, ValueError, KeyError) as error:
        failed = "the check could not finish: {}".format(error)
    if failed:
        if failed != CHECK_FAILED:
            say("claude-router: activate: {}".format(failed), sys.stderr)
        undo_journal(lay.state, state, manifest_path)
        label = CHECK_LINE.format if args.brief else "{:<7} {}".format
        say(label("undo", "{} restored; the router keeps its logins and assignments".format(shown(settings_path))), sys.stderr)
        say(label("next", "claude-router status"), sys.stderr)
        return 1
    write_journal(lay.state, dict(journal, phase="active", activated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    if args.brief:
        say(CHECK_LINE.format("activated", "Claude Code routes through the router"))
    else:
        say("done    clients connect through the router; undo with claude-router direct")
    return 0


def print_identity(args, state_root, home):
    helpers = payload_digest({relative: payload for relative, (payload, _) in helper_files().items()})
    if args.code_only:
        print(json.dumps({"code_sha256": code_sha256(), "helpers_sha256": helpers}, indent=2))
        return 0
    if args.claude is None or args.agent_sdk is None:
        raise Refusal("identity needs --claude and --agent-sdk, or --code-only")
    _, state = load_state(state_root)
    lay, record = installed_layout(state, state_root, home)
    _, status = running_status(lay.state)
    print(json.dumps(dict(measure(lay, record, status), clients=client_versions(args.claude, args.agent_sdk)), indent=2))
    return 0


def check(args, state_root, home, use_env):
    _, state = load_state(state_root)
    lay, _ = installed_layout(state, state_root, home)
    journal = read_journal(lay.state)
    settings_path = args.settings or (Path(journal["settings"]) if journal else default_settings(home, use_env))
    record_path = args.acceptance or lay.data / ACCEPTED
    stop = Stop()
    stop.install()
    next_step = run_check(lay, state, settings_path.absolute(), read_record(record_path), record_path, str(args.claude),
                          args.timeout, stop)
    if next_step == INTERRUPTED:
        say(CHECK_LINE.format("check", "interrupted"))
    elif next_step:
        say(CHECK_LINE.format("next", next_step))
    return 0 if next_step is None else 1


def default_settings(home, use_env):
    config = os.environ.get("CLAUDE_CONFIG_DIR") if use_env else None
    return (Path(config) if config else home / ".claude") / "settings.json"


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
    d.add_argument("--lock-fd", type=int, help="a descriptor of the activation lock that the caller holds")
    a = sub.add_parser("activate", help="point Claude Code at the router after acceptance passes")
    i = sub.add_parser("identity", help="print the installation's identity for an acceptance record")
    c = sub.add_parser("check", help="check that Claude Code routes through the router and send one test message")
    i.add_argument("--home", type=Path, help="alternate home; ignores configuration environment overrides")
    for p in (a, c):
        where = p.add_mutually_exclusive_group()
        where.add_argument("--home", type=Path, help="alternate home; ignores configuration environment overrides")
        where.add_argument("--manifest", type=Path, help="the astack manifest that records the installation; default from --home")
        p.add_argument("--claude", type=Path, default=Path("claude"), help="the Claude Code command that sends the test message")
        p.add_argument("--acceptance", type=Path, help="the acceptance record; default the installed accepted.json")
        p.add_argument("--settings", type=Path, help="Claude settings file; default $CLAUDE_CONFIG_DIR or ~/.claude, then settings.json")
    c.add_argument("--timeout", type=int, default=CHECK_TIMEOUT, help="seconds the test message may take")
    i.add_argument("--claude", type=Path, help="the terminal Claude Code executable to record")
    i.add_argument("--agent-sdk", type=Path, help="the installed @anthropic-ai/claude-agent-sdk package directory")
    i.add_argument("--code-only", action="store_true", help="print the checkout's code and helpers digests only")
    a.add_argument("--agent-sdk", type=Path, help=argparse.SUPPRESS)
    a.add_argument("--brief", action="store_true", help="print only the check lines and the result")
    a.add_argument("--project", type=Path, action="append", default=[], help="a project whose .claude settings to check; repeat")
    a.add_argument("--verify", nargs=2, action="append", default=[], metavar=("SESSION", "COMMAND"),
                   help="a shell command that continues the conversation with this Claude session ID; repeat per client")
    a.add_argument("--verify-timeout", type=int, default=600, help="seconds each --verify command may take")
    a.add_argument("--prepare", action="store_true", help="show the changes without making them")
    a.add_argument("--lock-fd", type=int, help="a descriptor of the activation lock that the caller holds")
    args = parser.parse_args()
    if args.command == "direct":
        return direct(args.state, args.manifest, "prepare" if args.prepare else "check" if args.check else "apply", args.stopped, args.lock_fd)
    home = (args.home or Path.home()).expanduser().resolve()
    use_env = args.home is None
    state_root = env_dir("XDG_STATE_HOME", ".local/state", home, use_env) / "astack"
    if args.command in ("activate", "check") and args.manifest:
        state_root = args.manifest.parent
    if args.command in ("activate", "identity", "check"):
        try:
            if args.command == "check":
                return check(args, state_root, home, use_env)
            return activate(args, state_root, home, use_env) if args.command == "activate" else print_identity(args, state_root, home)
        except Refusal as refusal:
            report(args.command, refusal, 10 if args.command == "check" or getattr(args, "brief", False) else 8)
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
    state_path, state, lock = locked_state(state_root, args.dry_run)
    with lock or nullcontext():
        recorded = state.get("components", {}).get(NAME, {}).get("layout")
        if command == "uninstall" and recorded and args.service not in (None, recorded["service"]):
            raise ValueError("--service {} differs from {}, the service this home's install recorded; omit --service".format(
                args.service, recorded["service"]))
        p = plan(command, state, lay, args.force, lock)
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


def cli():
    try:
        code = main()
    except (OSError, ValueError, KeyError) as error:
        print("astack: {}".format(error), file=sys.stderr)
        code = 1
    signal.pthread_sigmask(signal.SIG_BLOCK, STOPS)
    sys.exit(code)


if __name__ == "__main__":
    cli()
