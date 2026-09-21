"""Documentation maintenance for a repository whose docs grow one phase at a time.

Long-lived audit and implementation documents accumulate three specific kinds of
rot, and each subcommand here removes one:

* ``merge-blocks``    — many per-block reports that share one shape become a few
  themed documents with a table of contents.
* ``update-refs``     — rewrite every reference to the merged names, then delete
  the originals, and fail if anything still points at a removed file.
* ``duplicate-lessons`` — find lessons restated across phases, so a lesson list
  can be merged instead of appended to for ever.
* ``add-index``       — insert a phase index at the top of a long document.

Every subcommand prints what it did and refuses to finish quietly when something
does not add up (a lost line, a dangling reference, a pattern that never matched).
"""
from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, 'docs', 'development')

# --------------------------------------------------------------------- merging

GROUPS = [
    ('V05-BLOCKS-CORE-UZ.md',
     'v0.5 blok hisobotlari — yadro (graf, nazorat, aktivlar)',
     "Business Graph, agent nazorati, qoralama, ishchi kuchi, router va aktiv modeli. "
     "Har bir blok o'z davrida yozilgan; matni **o'zgartirilmagan**, faqat sarlavha "
     "darajalari birlashtirildi.",
     ['V05', 'V05B', 'V05C', 'V05D', 'V05E', 'V05F', 'V05G']),
    ('V05-BLOCKS-MESSAGING-UZ.md',
     'v0.5 blok hisobotlari — xabar almashish (WhatsApp, hujjat)',
     "WhatsApp kanali, kiruvchi oqim, oyna manbasi va hujjat qabul qilish. Bu bloklar "
     "bir oila: kanal, uning kiruvchi tomoni va oynani o'qish bir-birini tekshiradi.",
     ['V05H', 'V05I', 'V05J', 'V05K', 'V05L']),
    ('V05-BLOCKS-OPS-UZ.md',
     'v0.5 blok hisobotlari — operatsiyalar (ERP, ishlab chiqarish, aloqa, ombor)',
     "ERP posting, kechikkan ish eskalatsiyasi, ishlab chiqarish o'qishlari, OEE, "
     "telefoniya va ombor.",
     ['V05M', 'V05N', 'V05O', 'V05P', 'V05Q', 'V05R', 'V05S', 'V05T']),
]

HEADING = re.compile(r'^(#{1,6}) ', re.M)
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.workbuddy-ai'}


def _demote(text):
    """Shift every ATX heading down one level, so a merged file has one H1."""
    return HEADING.sub(lambda m: '#' * min(6, len(m.group(1)) + 1) + ' ', text)


def _body_lines(text):
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def cmd_merge_blocks(_args):
    stems_by_group = {name: stems for name, _, _, stems in GROUPS}
    originals = {f'{stem}-IMPLEMENTATION-UZ.md' for stems in stems_by_group.values()
                 for stem in stems}
    for filename, title, intro, stems in GROUPS:
        parts, toc = [], []
        for stem in stems:
            path = os.path.join(DOCS, f'{stem}-IMPLEMENTATION-UZ.md')
            if not os.path.exists(path):
                print(f'  {stem}: already merged, skipped')
                continue
            with io.open(path, encoding='utf-8') as handle:
                raw = handle.read()
            first = raw.lstrip().splitlines()[0].lstrip('# ').strip()
            toc.append(f'- [{stem} — {first}](#{stem.lower()})')
            parts.append(f'<a id="{stem.lower()}"></a>\n\n' + _demote(raw).strip() + '\n')
        if not parts:
            continue
        document = (f'# {title}\n\n{intro}\n\n## Mundarija\n\n' + '\n'.join(toc)
                    + '\n\n---\n\n' + '\n\n---\n\n'.join(parts))
        merged = set(_body_lines(document))
        missing = []
        for stem in stems:
            path = os.path.join(DOCS, f'{stem}-IMPLEMENTATION-UZ.md')
            if not os.path.exists(path):
                continue
            with io.open(path, encoding='utf-8') as handle:
                for line in _body_lines(_demote(handle.read())):
                    if line not in merged:
                        missing.append((stem, line[:70]))
        with io.open(os.path.join(DOCS, filename), 'w', encoding='utf-8') as handle:
            handle.write(document)
        print(f'{filename}: {len(stems)} reports, {len(_body_lines(document))} lines, '
              f'missing={len(missing)}')
        if missing:
            for stem, line in missing[:10]:
                print('   MISSING', stem, '|', line)
            sys.exit(1)
    print('merge complete; run "update-refs" next')
    return originals


def cmd_update_refs(_args):
    stem_to_group = {stem: name for name, _, _, stems in GROUPS for stem in stems}
    merged = {name for name, _, _, _ in GROUPS}
    originals = {f'{stem}-IMPLEMENTATION-UZ.md' for stem in stem_to_group}
    changed = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(('.md', '.json', '.py')) or name in originals:
                continue
            path = os.path.join(base, name)
            with io.open(path, encoding='utf-8') as handle:
                text = original = handle.read()
            for stem, target in sorted(stem_to_group.items(), key=lambda kv: -len(kv[0])):
                text = re.sub(r'\[([^\]]*?)\]\(' + stem + r'-IMPLEMENTATION-UZ\.md\)',
                              lambda m, a=f'{target}#{stem.lower()}': f'[{m.group(1)}]({a})',
                              text)
                text = text.replace(f'{stem}-IMPLEMENTATION-UZ.md', target)
            if text != original:
                with io.open(path, 'w', encoding='utf-8') as handle:
                    handle.write(text)
                changed.append(os.path.relpath(path, ROOT))
    print(f'rewrote {len(changed)} files')
    removed = 0
    for stem in stem_to_group:
        path = os.path.join(DOCS, f'{stem}-IMPLEMENTATION-UZ.md')
        if os.path.exists(path):
            os.remove(path)
            removed += 1
    print(f'removed {removed} originals')
    leftover = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(('.md', '.json', '.py')):
                continue
            with io.open(os.path.join(base, name), encoding='utf-8', errors='ignore') as fh:
                text = fh.read()
            for stem in stem_to_group:
                if f'{stem}-IMPLEMENTATION-UZ.md' in text:
                    leftover.append((os.path.relpath(os.path.join(base, name), ROOT), stem))
    if leftover:
        for path, stem in leftover:
            print('  LEFTOVER', path, '->', stem)
        sys.exit(1)
    print('OK: no reference points at a removed file')
    return merged


# ------------------------------------------------------------ duplicate lessons

ITEM = re.compile(r'^\s*(?:\d+\.|[-*])\s+(.*\S)\s*$')
CONTINUATION = re.compile(r'^\s{2,}(\S.*\S)\s*$')
STOP = {'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'is', 'it', 'that', 'this',
        'for', 'on', 'be', 'are', 'not', 'as', 'with', 'at', 'by', 'from', 'bu',
        'va', 'bir', 'uni', 'u', 'ning', 'ham', 'emas', 'kerak', 'bo', 'lsa', 'ni'}


def _tokens(text):
    return {w for w in re.findall(r'[a-zA-Zа-яА-Я0-9_]+', text.lower())
            if w not in STOP and len(w) > 2}


def _lesson_items(lines):
    """Lesson items, continuation lines joined -- reading only the first line of a
    multi-line item is a false negative that hides real duplicates."""
    in_lesson, out, current = False, [], None
    for number, line in enumerate(lines, 1):
        if re.match(r'^#{1,4}\s', line):
            if current:
                out.append(current)
                current = None
            in_lesson = bool(re.search(r'[Ss]aboq', line))
            continue
        if not in_lesson:
            continue
        match = ITEM.match(line)
        if match:
            if current:
                out.append(current)
            current = (number, match.group(1))
            continue
        cont = CONTINUATION.match(line)
        if cont and current:
            current = (current[0], current[1] + ' ' + cont.group(1))
        elif line.strip() and current:
            out.append(current)
            current = None
    if current:
        out.append(current)
    return [(n, t) for n, t in out if len(t) >= 25]


def cmd_duplicate_lessons(args):
    path = args[0] if args else os.path.join(DOCS, 'ULTRA-AUDIT-ASCII-CELL-UZ.md')
    threshold = float(os.environ.get('LESSON_SIMILARITY', '0.55'))
    with io.open(path, encoding='utf-8') as handle:
        lessons = _lesson_items(handle.read().splitlines())

    def jaccard(left, right):
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    groups, used = [], set()
    for i, (_, text) in enumerate(lessons):
        if i in used:
            continue
        group = [lessons[i]]
        for j in range(i + 1, len(lessons)):
            if j not in used and jaccard(_tokens(text), _tokens(lessons[j][1])) >= threshold:
                group.append(lessons[j])
                used.add(j)
        used.add(i)
        if len(group) > 1:
            groups.append(group)
    print(f'{os.path.relpath(path, ROOT)}: {len(lessons)} lesson lines, '
          f'{len(groups)} duplicate groups (threshold {threshold})\n')
    for group in sorted(groups, key=len, reverse=True):
        print(f'--- {len(group)} near-duplicates ---')
        for line_no, text in group:
            print(f'  L{line_no:5d}  {text[:105]}')
        print()


# --------------------------------------------------------------------- indexing

def _headings_outside_fences(lines):
    inside = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith('```'):
            inside = not inside
            continue
        if inside:
            continue
        if re.match(r'^#{1,2} ', line):
            yield number, line


def cmd_add_index(args):
    path = args[0]
    with io.open(path, encoding='utf-8') as handle:
        lines = handle.read().splitlines()
    if any('Mundarija (fazalar)' in line for line in lines[:40]):
        print('index already present, nothing to do')
        return
    entries = []
    for number, line in _headings_outside_fences(lines):
        level = len(line) - len(line.lstrip('#'))
        title = line.lstrip('# ').strip()
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:60]
        if level == 1 and number > 1:
            entries.append(('phase', title, slug, number))
        elif level == 2 and re.match(r'^§\d+\.', title):
            entries.append(('section', title, slug, number))
    if not entries:
        print('no headings found')
        return
    block = ['## Mundarija (fazalar)', '']
    for kind, title, slug, number in entries:
        if kind == 'phase':
            block.append(f'- **[{title}](#{slug})**')
        elif re.match(r'^§\d+\.', title):
            short = re.sub(r'^§(\d+)\.\s*', r'§\1 ', title)
            block.append(f'  - [{short}](#{slug})')
    block.append('')
    # Insert after the H1 and any blockquote intro that follows it.
    insert_at = 1
    while insert_at < len(lines) and (not lines[insert_at].strip()
                                      or lines[insert_at].startswith('>')):
        insert_at += 1
    out = lines[:insert_at] + [''] + block + lines[insert_at:]
    with io.open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(out) + '\n')
    print(f'inserted index: {len(entries)} entries into {os.path.relpath(path, ROOT)}')


COMMANDS = {
    'merge-blocks': cmd_merge_blocks,
    'update-refs': cmd_update_refs,
    'duplicate-lessons': cmd_duplicate_lessons,
    'add-index': cmd_add_index,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print('subcommands:', ', '.join(COMMANDS))
        sys.exit(2)
    COMMANDS[sys.argv[1]](sys.argv[2:])
