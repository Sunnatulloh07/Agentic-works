"""Append a section to a document, exactly once, preserving the file's newlines.

Written after losing a run to the same mistake twice: a heredoc or ``sed`` append
that executed twice duplicated a whole session log into ``PROGRESS-UZ.md``, and
the only reason the second one was caught is that the log happened to be grepped
for a header count afterwards.

So the rule is mechanical rather than careful: give the section a **marker**, and
this refuses to append if the marker is already present. A duplicate is now an
error message instead of a corrupted canonical record.

Usage::

    python scripts/append_doc_section.py <target.md> <section.md> --marker "§155."

The target keeps whatever line ending it already uses; the section file is read as
text and re-encoded to match, so a section authored with LF does not introduce a
mixed-ending file.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('target')
    parser.add_argument('section')
    parser.add_argument('--marker', required=True,
                        help='string that must NOT already be in the target')
    args = parser.parse_args()

    target = args.target if os.path.isabs(args.target) else os.path.join(ROOT, args.target)
    section = args.section if os.path.isabs(args.section) else os.path.join(ROOT, args.section)

    with open(target, 'rb') as handle:
        before = handle.read()
    newline = b'\r\n' if b'\r\n' in before else b'\n'
    marker = args.marker.encode('utf-8')

    if marker in before:
        print(f'REFUSED: {os.path.relpath(target, ROOT)} already contains '
              f'{args.marker!r} -- not appending twice.')
        return 1

    with open(section, 'rb') as handle:
        text = handle.read()
    # Normalise the section to LF, then to the target's ending, so a section that
    # was authored on either platform lands the same way.
    text = text.replace(b'\r\n', b'\n')
    text = newline.join(text.split(b'\n'))
    if not text.endswith(newline):
        text += newline

    with open(target, 'wb') as handle:
        handle.write(before + text)

    after = os.path.getsize(target)
    print(f'appended {len(text)} bytes to {os.path.relpath(target, ROOT)} '
          f'({len(before)} -> {after}); newline={"CRLF" if newline == b"\r\n" else "LF"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
