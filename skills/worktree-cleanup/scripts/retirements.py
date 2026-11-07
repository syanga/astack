import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def git(directory, *args, git_dir=False):
    command = ['git', '--git-dir' if git_dir else '-C', str(directory), *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git lookup failed')
    return result.stdout.strip()


def worktrees(common):
    rows = []
    row = {}
    for field in git(common, 'worktree', 'list', '--porcelain', '-z', git_dir=True).split('\0'):
        if field:
            key, _, value = field.partition(' ')
            row[key] = value
        elif row:
            rows.append(row)
            row = {}
    if row:
        rows.append(row)
    return rows


def branch(directory):
    result = subprocess.run(['git', '-C', str(directory), 'symbolic-ref', '--quiet', 'HEAD'],
                            capture_output=True, text=True, timeout=30)
    return result.stdout.strip() or None


def retaining_refs(common, head):
    return git(common, 'for-each-ref', '--format=%(refname)', '--contains', head,
               'refs/heads', git_dir=True).splitlines()


def record_path(state, worktree):
    key = hashlib.sha256(os.fsencode(str(worktree))).hexdigest()
    return state / (key + '.json')


def read_record(path):
    if path.is_symlink():
        raise ValueError('Retirement record is a symlink: ' + str(path))
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except RecursionError as error:
        raise ValueError('Retirement record is too deeply nested: ' + str(path)) from error
    required = ['worktree', 'common', 'head', 'owner', 'authorization', 'queued_at']
    if (not isinstance(row, dict) or set(row) != {'version', 'branch', *required} or
            row.get('version') != 1 or any(not isinstance(row.get(k), str) or not row[k] for k in required)):
        raise ValueError('Invalid retirement record: ' + str(path))
    if 'branch' not in row or (row['branch'] is not None and not isinstance(row['branch'], str)):
        raise ValueError('Invalid branch in retirement record')
    if not Path(row['worktree']).is_absolute() or not Path(row['common']).is_absolute():
        raise ValueError('Retirement paths must be absolute')
    return row


def snapshot(path):
    common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    root = Path(git(path, 'rev-parse', '--show-toplevel')).resolve()
    if root != path:
        raise ValueError('Use the worktree root')
    rows = worktrees(common)
    own = next((r for r in rows if Path(r['worktree']).resolve() == path), None)
    if own is None or Path(rows[0]['worktree']).resolve() == path:
        raise ValueError('Keep the primary checkout')
    if 'locked' in own:
        raise ValueError('Keep locked worktrees')
    ref = branch(path)
    default_refs = {'refs/heads/main', 'refs/heads/master'}
    for line in git(path, 'for-each-ref', '--format=%(refname) %(symref)', 'refs/remotes').splitlines():
        name, _, target = line.partition(' ')
        prefix = name.removesuffix('HEAD')
        if name.endswith('/HEAD') and target.startswith(prefix):
            default_refs.add('refs/heads/' + target[len(prefix):])
    if ref in default_refs:
        raise ValueError('Keep default-branch worktrees')
    entries = git(path, 'ls-files', '-v', '-z').split('\0')
    if any(entry and (entry[0].islower() or entry[0] == 'S') for entry in entries):
        raise ValueError('Review assume-unchanged and skip-worktree entries before queuing retirement')
    if git(path, 'status', '--porcelain=v1', '--untracked-files=normal'):
        raise ValueError('Resolve tracked and untracked changes before queuing retirement')
    head = git(path, 'rev-parse', 'HEAD')
    if not retaining_refs(common, head):
        raise ValueError('Retain this commit through a local branch first')
    return {'worktree': str(path), 'common': str(common), 'head': head, 'branch': ref}


def queue(state, args):
    path = Path(args.worktree).expanduser().resolve()
    if state == path or state.is_relative_to(path):
        raise ValueError('Keep retirement state outside the target checkout')
    row = dict(snapshot(path), version=1, owner=args.owner.strip(),
               authorization=args.authorization.strip(), queued_at=datetime.now(timezone.utc).isoformat())
    if not row['owner'] or not row['authorization']:
        raise ValueError('Owner and prior cleanup authorization are required')
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = record_path(state, path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=state, delete=False) as f:
            temporary = Path(f.name)
            json.dump(row, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            old = read_record(destination)
            if any(old.get(k) != row.get(k) for k in ['worktree', 'common', 'head', 'branch', 'owner', 'authorization']):
                raise ValueError('Conflicting retirement; verify and withdraw the old record first')
            row = old
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return row


def inspect(row):
    path = Path(row['worktree'])
    if not path.exists():
        return 'absent'
    try:
        current = snapshot(path)
        if any(current[k] != row[k] for k in ['worktree', 'common', 'head', 'branch']):
            return 'changed'
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 'held'
    return 'pending'


def finish(state, args):
    path = Path(args.worktree).expanduser().resolve()
    file = record_path(state, path)
    row = read_record(file)
    if row['owner'] != args.owner or row['head'] != args.head:
        raise ValueError('Owner or recorded head does not match')
    if args.command == 'complete':
        if path.exists() or path.is_symlink():
            raise ValueError('Worktree still exists; keep the retirement record')
        if any(Path(r['worktree']).resolve() == path for r in worktrees(row['common'])):
            raise ValueError('Worktree remains registered; keep the retirement record')
        if not retaining_refs(row['common'], row['head']):
            raise ValueError('Retain the recorded commit through a local branch first')
    file.unlink()
    return {'worktree': str(path), 'status': 'completed' if args.command == 'complete' else 'withdrawn'}


def main():
    parser = argparse.ArgumentParser(description='Track deferred worktree retirement; never removes a checkout.')
    default = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'astack/worktree-cleanup'
    parser.add_argument('--state-dir', type=Path, default=default)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('queue')
    add.add_argument('--worktree', required=True)
    add.add_argument('--owner', required=True)
    add.add_argument('--authorization', required=True)
    commands.add_parser('list')
    for name in ['complete', 'withdraw']:
        command = commands.add_parser(name)
        command.add_argument('--worktree', required=True)
        command.add_argument('--owner', required=True)
        command.add_argument('--head', required=True)
    args = parser.parse_args()
    state = args.state_dir.expanduser().resolve()
    try:
        if args.command == 'queue':
            result = queue(state, args)
        elif args.command == 'list':
            result = []
            for file in sorted(state.glob('*.json')):
                try:
                    row = read_record(file)
                    result.append(dict(row, status=inspect(row)))
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    result.append({'record': str(file), 'status': 'held', 'error': str(error)})
        else:
            result = finish(state, args)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
