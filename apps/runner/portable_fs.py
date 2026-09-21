"""Fixed-operation POSIX filesystem helper. No shell, write, app or screen tool.

Linux uses /proc descriptor identity; macOS uses fcntl F_GETPATH. macOS code is
source-reviewed only until run on a real Mac. Launch with an explicit Python 3.
"""
import json
import os
from pathlib import Path
import stat
import sys

MAX_BYTES = 16000
MAX_ENTRIES = 200
MAX_VISITED = 1000
DENY = ('.env', '.ssh', '.aws', 'credentials', 'password', 'parol', 'cvv')


def fd_path(fd):
    if sys.platform.startswith('linux'):
        return os.path.realpath('/proc/self/fd/' + str(fd))
    if sys.platform == 'darwin':
        import fcntl
        # F_GETPATH is the Darwin fcntl descriptor-to-path operation (MAXPATHLEN).
        value = fcntl.fcntl(fd, 50, bytes(1024))
        return os.fsdecode(value.split(bytes(1), 1)[0])
    raise ValueError('No verified descriptor resolver for this operating system')


def roots_and_deny(config):
    if not isinstance(config, dict) or set(config) - {'folders', 'deny_always'}:
        raise ValueError('Invalid filesystem configuration')
    folders = config.get('folders')
    deny = config.get('deny_always', [])
    if not isinstance(folders, list) or not 1 <= len(folders) <= 32:
        raise ValueError('Explicit bounded root folders required')
    if not isinstance(deny, list) or len(deny) > 100 or any(not isinstance(v, str) or not v or len(v) > 128 for v in deny):
        raise ValueError('Invalid denied path patterns')
    roots = []
    for folder in folders:
        if not isinstance(folder, str) or not os.path.isabs(folder) or len(folder) > 1000:
            raise ValueError('Absolute root required')
        canonical = os.path.realpath(folder)
        if canonical != os.path.normpath(folder) or canonical == os.path.sep or not os.path.isdir(canonical):
            raise ValueError('Pinned canonical dedicated directory required')
        roots.append(canonical)
    return roots, DENY + tuple(v.lower() for v in deny)


def permitted(value, roots, deny):
    if not isinstance(value, str) or not os.path.isabs(value) or len(value) > 2000 or '\x00' in value:
        raise ValueError('Absolute bounded path required')
    canonical = os.path.realpath(value)
    if any(word in canonical.lower() for word in deny): raise PermissionError('Sensitive path denied')
    if not any(os.path.commonpath([root, canonical]) == root for root in roots):
        raise PermissionError('Path outside approved roots')
    return canonical


def verified_fd(fd, roots, deny):
    resolved = permitted(fd_path(fd), roots, deny)
    current, opened = os.stat(resolved, follow_symlinks=False), os.fstat(fd)
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise PermissionError('Descriptor identity changed')
    return resolved, opened


def execute(tool, args, config):
    roots, deny = roots_and_deny(config)
    if tool not in {'fs.list', 'fs.read_text'} or not isinstance(args, dict):
        raise PermissionError('Unsupported filesystem operation')
    field = 'dir' if tool == 'fs.list' else 'file'
    if set(args) != {field}: raise ValueError('Unexpected filesystem arguments')
    target = permitted(args[field], roots, deny)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if tool == 'fs.list': flags |= os.O_DIRECTORY
    fd = os.open(target, flags)
    try:
        resolved, opened = verified_fd(fd, roots, deny)
        if tool == 'fs.read_text':
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > MAX_BYTES:
                raise PermissionError('Only small single-link regular text files allowed')
            data = bytearray()
            while len(data) <= MAX_BYTES:
                part = os.read(fd, MAX_BYTES + 1 - len(data))
                if not part: break
                data.extend(part)
            if len(data) > MAX_BYTES or 0 in data:
                raise ValueError('Oversized or binary file denied')
            verified_fd(fd, roots, deny)
            return {'text': bytes(data).decode('utf-8', errors='strict')}
        if not stat.S_ISDIR(opened.st_mode): raise PermissionError('Directory required')
        entries = []
        visited = 0
        truncated = False
        with os.scandir(fd) as scan:
            for entry in scan:
                visited += 1
                if visited > MAX_VISITED or len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                if entry.is_symlink(): continue
                try:
                    permitted(os.path.join(resolved, entry.name), roots, deny)
                    meta = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(meta.st_mode) and meta.st_nlink != 1: continue
                    if not (stat.S_ISREG(meta.st_mode) or stat.S_ISDIR(meta.st_mode)): continue
                except (OSError, ValueError, PermissionError):
                    continue
                entries.append({'name': entry.name, 'type': 'directory' if stat.S_ISDIR(meta.st_mode) else 'file'})
        verified_fd(fd, roots, deny)
        return {'entries': entries, 'truncated': truncated}
    finally:
        os.close(fd)


def main():
    try:
        data = sys.stdin.buffer.read(64001)
        if len(data) > 64000: raise ValueError('Input limit')
        request = json.loads(data)
        if not isinstance(request, dict) or set(request) != {'tool', 'args', 'config'}:
            raise ValueError('Invalid helper request')
        output = execute(request['tool'], request['args'], request['config'])
        print(json.dumps({'ok': True, 'result': output}, ensure_ascii=False))
        return 0
    except Exception:
        # Neither secrets, filenames nor Python tracebacks leave the helper.
        print(json.dumps({'ok': False, 'error': 'local_policy_or_execution_error'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
