"""Regenerate ``MANIFEST.sha256`` from the working tree.

The checker (``verify_manifest.py``) had no counterpart, so the manifest could only be
produced outside the repository. Every phase of work then left it further behind: 51
hashes drifted and 162 files were unlisted before this script existed, and the gate was
red for so long that red stopped carrying information.

Two modes, because a generator that can only write is a generator nobody runs on a
schedule::

    python scripts/generate_manifest.py            # rewrite MANIFEST.sha256
    python scripts/generate_manifest.py --check     # exit 1 if it is stale, write nothing

The file list is exactly ``verify_manifest.release_files`` -- the same enumeration the
checker uses. Sorted by path so the output is byte-identical for an identical tree, and
written as LF with a trailing newline so a regeneration on Windows does not show up as
a whole-file diff on Linux.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_manifest import digest, release_files  # noqa: E402  (path set above)

MANIFEST_NAME = 'MANIFEST.sha256'


def render(root):
    """Return the manifest text for ``root`` as a list of ``'<sha>  <path>'`` lines."""
    files = release_files(root)
    lines = []
    for name in sorted(files):
        path = files[name]
        if path.is_symlink() or not path.is_file():
            # The checker refuses symlinks outright, so emitting one would only move the
            # failure from "unlisted" to "symlink denied". Skip it and stay quiet here;
            # the checker is the place that reports it.
            continue
        lines.append(digest(path) + '  ' + name)
    return lines


def write(root, lines):
    (root / MANIFEST_NAME).write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check', action='store_true',
                        help='report staleness without writing; non-zero exit if stale')
    args = parser.parse_args()
    root = args.root.resolve()
    lines = render(root)
    if args.check:
        current = root / MANIFEST_NAME
        if not current.is_file():
            print('stale: %s is missing' % MANIFEST_NAME)
            return 1
        existing = current.read_text(encoding='utf-8').splitlines()
        if existing == lines:
            print('current: %d files' % len(lines))
            return 0
        added = len(set(lines) - set(existing))
        removed = len(set(existing) - set(lines))
        print('stale: %d lines on disk, %d expected (+%d/-%d)'
              % (len(existing), len(lines), added, removed))
        print('run: python scripts/generate_manifest.py')
        return 1
    write(root, lines)
    print('wrote %s: %d files' % (MANIFEST_NAME, len(lines)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
