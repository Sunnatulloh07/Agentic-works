"""A customer's display name, made safe to store and show.

Telegram (``from.first_name``) and WhatsApp (``contacts[].profile.name``) let the
customer choose this text, so it is DATA: it never authenticates anyone (the sender
id does) and never reaches a model as an instruction. It is stored bounded and
stripped of anything that can hide or reorder text.
"""
import unicodedata

MAX_DISPLAY_NAME_CHARS = 64
# Cc control, Cf format (bidi overrides, zero-width), Zl/Zp line and paragraph
# separators; Co/Cs/Cn are never text either.
_STRIPPED = frozenset(('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp'))


def clean_display_name(*parts) -> str:
    """Join the string parts with single spaces, drop invisible characters, cap the length.

    Non-string parts are ignored, so a hostile payload (a number, a list) yields ''.
    """
    words = []
    for part in parts:
        if isinstance(part, str):
            # Every stripped character is dropped BEFORE splitting, and whitespace is
            # collapsed: '\n' is Cc, so it would otherwise glue two words together.
            spaced = ''.join(' ' if ch.isspace() else ch for ch in part[:MAX_DISPLAY_NAME_CHARS * 4]
                             if ch.isspace() or unicodedata.category(ch) not in _STRIPPED)
            words.extend(spaced.split())
    return ' '.join(words)[:MAX_DISPLAY_NAME_CHARS].strip()
