import hashlib
import json
import os
import stat
import tempfile


def fingerprint(data, mode):
    return hashlib.sha256(data + str(mode).encode()).hexdigest()


def current_hash(path):
    if path.is_symlink():
        return "symlink"
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Expected a file at {}".format(path))
    return fingerprint(path.read_bytes(), stat.S_IMODE(path.stat().st_mode))


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


def save_manifest(path, state):
    atomic_write(path, (json.dumps(state, indent=2) + "\n").encode(), 0o600)
