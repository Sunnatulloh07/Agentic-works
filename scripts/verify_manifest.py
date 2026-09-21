"""Verify release SHA-256 coverage. This detects changes, not publisher authenticity.

Run on a freshly extracted release ZIP. Runtime/build outputs are excluded only
in the explicit directory list below. Regenerating a manifest does not establish
that a historical source was trustworthy.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

EXCLUDED_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', '.next', '.pytest_cache'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


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
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts) or str(rel) == 'MANIFEST.sha256':
            continue
        if path.is_symlink():
            errors.append('Symlink denied: ' + rel.as_posix())
        elif path.is_file():
            actual[rel.as_posix()] = path
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
