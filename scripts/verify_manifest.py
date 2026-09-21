"""Verify release SHA-256 coverage. This detects changes, not publisher authenticity.

Run on a freshly extracted release ZIP. Runtime/build outputs are excluded by the
explicit directory list below, and by the repository's own ignore rules. Regenerating
a manifest does not establish that a historical source was trustworthy.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

EXCLUDED_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', '.next', '.pytest_cache'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def ignored_paths(root):
    """Repository-declared non-release paths (``.gitignore``) as posix relative names.

    ``EXCLUDED_DIRS`` is a hardcoded guess at "build and runtime output". The
    repository's own ignore rules are the authoritative statement of the same thing,
    and a working tree keeps that output on disk: bytecode caches, pytest caches,
    captured verification logs, scratch databases. Without this the manifest could only
    be checked against a fresh extract, so the gate was unrunnable in the tree the code
    is actually written in -- which is how it came to sit at FAIL unnoticed.

    Guarded twice. The query is skipped unless ``root`` is itself the repository top
    level, because ``git -C`` walks parent directories and a release archive extracted
    inside another checkout would otherwise inherit that checkout's ignore rules. Every
    failure path returns an empty set, which is exactly the previous behaviour.
    """
    try:
        top = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, timeout=30)
        if top.returncode or Path(top.stdout.strip()).resolve() != Path(root).resolve():
            return set()
        proc = subprocess.run(['git', '-C', str(root), 'ls-files', '--others', '--ignored',
                               '--exclude-standard', '-z'],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode:
        return set()
    return {name for name in proc.stdout.split('\0') if name}


def release_files(root):
    """Yield ``(posix_relative_name, path)`` for every file a release archive carries.

    One enumeration serves both the checker and the generator, so the two cannot drift
    into disagreeing about what "the release" is -- the failure mode that made the old
    manifest unusable in the first place.
    """
    skip = ignored_paths(root)
    found = {}
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        name = rel.as_posix()
        if any(part in EXCLUDED_DIRS for part in rel.parts) or name == 'MANIFEST.sha256':
            continue
        if name in skip:
            continue
        found[name] = path
    return found


def verify(root):
    root = Path(root).resolve()
    manifest = root / 'MANIFEST.sha256'
    errors, expected = [], {}
    if not manifest.is_file() or manifest.is_symlink():
        return {'status': 'FAIL', 'checked': 0, 'errors': ['Manifest missing or symlink']}
    for line_no, line in enumerate(manifest.read_text(encoding='utf-8').splitlines(), 1):
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            errors.append('Invalid manifest line ' + str(line_no))
            continue
        sha, filename = match.groups()
        rel = PurePosixPath(filename)
        if (rel.is_absolute() or '..' in rel.parts or filename != str(rel)
                or '\\' in filename or filename == 'MANIFEST.sha256'
                or any(p in EXCLUDED_DIRS for p in rel.parts)):
            errors.append('Unsafe manifest entry at line ' + str(line_no))
            continue
        if filename in expected:
            errors.append('Duplicate manifest entry: ' + filename)
            continue
        expected[filename] = sha
    actual = {}
    for name, path in release_files(root).items():
        if path.is_symlink():
            errors.append('Symlink denied: ' + name)
        elif path.is_file():
            actual[name] = path
    for name, sha in expected.items():
        if name not in actual:
            errors.append('Missing file: ' + name)
        elif digest(actual[name]) != sha:
            errors.append('Hash mismatch: ' + name)
    for name in sorted(actual.keys() - expected.keys()):
        errors.append('Unlisted file: ' + name)
    return {'status': 'FAIL' if errors else 'PASS', 'checked': len(expected), 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = verify(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
