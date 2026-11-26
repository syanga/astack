import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

STATE = Path(os.environ["FAKE_BOAT_DIR"])
BOX_HOME = "/home/user/"
STEPS = {"_box prepare": "setup", "_box run": "run"}


def emit(value):
    print(json.dumps(value), flush=True)


def fail(code, status, message):
    emit({"code": code, "error": message, "event": "error", "status": status})
    sys.exit(1)


def take(key):
    path = STATE / "plan.json"
    plan = json.loads(path.read_text()) if path.exists() else {}
    queue = plan.get(key) or []
    if not queue:
        return None
    plan[key] = queue[1:]
    path.write_text(json.dumps(plan))
    return queue[0]


def box(box_id):
    path = STATE / "boxes" / box_id
    if not path.is_dir():
        fail("not_found", 404, "not found (404)")
    return path


def box_env(path):
    home = str(path / "home")
    machine = os.environ.get("FAKE_BOAT_BOX_MACHINE", os.environ.get("JOBGATE_MACHINE_FILE", ""))
    return dict(os.environ, HOME=home, JOBGATE_HOME=home, PATH=os.environ["FAKE_BOAT_BOX_PATH"],
                JOBGATE_MACHINE_FILE=machine)


def option(args, name):
    return args[args.index(name) + 1] if name in args else None


def new(args):
    source = option(args, "--from")
    if source is not None and not (STATE / "saved" / source).is_dir():
        fail("snapshot_not_found", 404, 'No saved snapshot "{}".'.format(source))
    error = take("new")
    if error and not error.get("after_created"):
        fail(error["code"], error["status"], error["error"])
    count = len(list((STATE / "boxes").glob("bx_*"))) if (STATE / "boxes").is_dir() else 0
    box_id = "bx_{}{}".format(count + 1, os.getpid())
    path = STATE / "boxes" / box_id
    path.mkdir(parents=True)
    if source is None:
        (path / "home").mkdir()
    else:
        shutil.copytree(str(STATE / "saved" / source / "home"), str(path / "home"), symlinks=True)
    emit({"event": "created", "id": box_id, "ttlSeconds": int(option(args, "--ttl") or 0)})
    if error:
        if "detail" in error:
            print(error["detail"], file=sys.stderr)
        fail(error["code"], error["status"], error["error"])
    hold = take("hold_new")
    if hold:
        (STATE / "new-held").touch()
        time.sleep(hold)
    emit({"event": "ready", "id": box_id, "state": "ready"})


def detach(path, command):
    procs = path / "procs"
    procs.mkdir(exist_ok=True)
    process = subprocess.Popen([sys.executable, __file__, "_wrap", str(procs), "--", *command], cwd=str(path / "home"),
                               env=box_env(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    (procs / "{}.command".format(process.pid)).write_text(" ".join(command))
    emit({"pid": process.pid, "processId": process.pid, "success": True})


def wrap(procs, command):
    pid = os.getpid()
    with open(os.path.join(procs, "{}.out".format(pid)), "wb") as out, \
            open(os.path.join(procs, "{}.err".format(pid)), "wb") as err:
        code = subprocess.run(command, stdout=out, stderr=err).returncode
    with open(os.path.join(procs, "{}.exit.tmp".format(pid)), "w") as stream:
        stream.write(str(code))
    os.replace(os.path.join(procs, "{}.exit.tmp".format(pid)), os.path.join(procs, "{}.exit".format(pid)))


def vanish(path):
    for pid_file in (path / "procs").glob("*.command"):
        try:
            os.killpg(int(pid_file.name.split(".")[0]), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    for _ in range(50):
        shutil.rmtree(str(path), ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.1)


def attach(path, pid):
    procs = path / "procs"
    command = (procs / "{}.command".format(pid)).read_text()
    step = next((name for marker, name in STEPS.items() if marker in command), None)
    emit({"processId": int(pid), "stdoutOffset": 0, "stderrOffset": 0, "type": "attached"})
    offsets = {"stdout": 0, "stderr": 0}
    plan_path = STATE / "plan.json"
    planned = json.loads(plan_path.read_text()).get("vanish", []) if plan_path.exists() else []
    if planned and planned[0] == step:
        take("vanish")
        time.sleep(0.2)
        vanish(path)
        fail("not_found", 404, "the connection to the box closed")
    out_of_memory = json.loads(plan_path.read_text()).get("oom", []) if plan_path.exists() else []
    if out_of_memory and out_of_memory[0] == step:
        take("oom")
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        (procs / "{}.oom".format(pid)).touch()
        emit({"exitCode": None, "signal": "SIGKILL", "status": "exited", "type": "exit"})
        sys.exit(137)
    while True:
        finished = (procs / "{}.exit".format(pid)).exists()
        for kind, suffix in (("stdout", "out"), ("stderr", "err")):
            try:
                with open(str(procs / "{}.{}".format(pid, suffix)), "rb") as stream:
                    stream.seek(offsets[kind])
                    data = stream.read()
            except FileNotFoundError:
                data = b""
            if data:
                offsets[kind] += len(data)
                emit({"data": data.decode("utf-8", "replace"), "nextOffset": offsets[kind], "type": kind})
        if finished:
            code = int((procs / "{}.exit".format(pid)).read_text())
            emit({"exitCode": code, "signal": None, "status": "exited", "type": "exit"})
            sys.exit(code)
        if not path.is_dir():
            fail("not_found", 404, "the box is gone")
        time.sleep(0.05)


def status(path, pid):
    if (path / "procs" / "{}.oom".format(pid)).exists():
        emit({"running": False, "exitCode": None, "oomKilled": True, "signal": "SIGKILL"})
        return
    exit_file = path / "procs" / "{}.exit".format(pid)
    code = int(exit_file.read_text()) if exit_file.exists() else None
    emit({"running": code is None, "exitCode": code, "oomKilled": False, "signal": None})


def exec_(args):
    path = box(args[0])
    if "--attach" in args:
        attach(path, option(args, "--attach"))
    elif "--status" in args:
        status(path, option(args, "--status"))
    elif "--detach" in args:
        detach(path, args[args.index("--") + 1:])
    else:
        result = subprocess.run(args[args.index("--") + 1:], cwd=str(path / "home"), env=box_env(path),
                                capture_output=True, text=True)
        emit({"exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "oomKilled": False,
              "signal": None})
        sys.exit(result.returncode)


def scp(args):
    source, target = args
    box_id, _, remote = target.partition(":")
    path = box(box_id)
    if not remote.startswith(BOX_HOME):
        fail("bad_path", 400, "the fake copies only into " + BOX_HOME)
    destination = path / "home" / remote[len(BOX_HOME):]
    shutil.copyfile(source, str(destination))
    uploads = STATE / "uploads"
    uploads.mkdir(exist_ok=True)
    shutil.copyfile(source, str(uploads / "{}-{}".format(len(list(uploads.iterdir())) + 1, destination.name)))


def snapshot(args):
    if args[0] == "rm":
        shutil.rmtree(str(STATE / "saved" / args[1]), ignore_errors=True)
        emit({"removed": args[1]})
        return
    error = take("save")
    if error:
        fail(error["code"], error["status"], error["error"])
    path = box(args[0])
    saved = STATE / "saved" / args[1]
    shutil.rmtree(str(saved), ignore_errors=True)
    saved.mkdir(parents=True)
    shutil.copytree(str(path / "home"), str(saved / "home"), symlinks=True)
    emit({"status": "saving", "snapshot": {"name": args[1], "status": "saving"}})


def snapshots():
    saved = sorted(path.name for path in (STATE / "saved").iterdir()) if (STATE / "saved").is_dir() else []
    emit({"named": [{"name": name, "status": "ready"} for name in saved], "snapshots": []})


def main(argv):
    if argv[:1] == ["_wrap"]:
        wrap(argv[1], argv[3:])
        return
    STATE.mkdir(parents=True, exist_ok=True)
    with open(str(STATE / "calls.jsonl"), "a") as log:
        log.write(json.dumps(argv) + "\n")
    args = [arg for arg in argv if arg not in ("--json", "--no-update")]
    if args[0] == "new":
        new(args[1:])
    elif args[0] == "exec":
        exec_(args[1:])
    elif args[0] == "scp":
        scp(args[1:])
    elif args[0] == "extend":
        box(args[1])
        emit({"ok": True})
    elif args[0] == "delete":
        vanish(box(args[1]))
        emit({"operation": {"status": "pending", "targetId": args[1]}})
    elif args[0] == "snapshot":
        snapshot(args[1:])
    elif args[0] == "snapshots":
        snapshots()
    else:
        fail("unknown_command", 400, "the fake has no command " + args[0])


if __name__ == "__main__":
    main(sys.argv[1:])
