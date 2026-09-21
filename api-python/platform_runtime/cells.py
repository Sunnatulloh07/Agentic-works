"""What a cell in someone else's system is allowed to mean.

This module exists because of one measured defect, not one opinion. Four modules
(``erp``, ``documents``, ``escalation`` and the ``inventory``/``oee``/
``manufacturing`` number readers) each interpreted a *cell* -- a value read out of
an ERP, a spreadsheet or a database -- with a regular expression that looked
ASCII and was not.

In Python, ``re`` is Unicode-aware by default: ``\\d`` matches the whole ``Nd``
(decimal digit) category, not ``[0-9]``. And ``float()`` normalises those digits.
So with the ordinary patterns, every one of these was accepted:

    ``'१२'``  (Devanagari)      -> 12.0
    ``'١٢'``  (Arabic-Indic)    -> 12.0
    ``'１２'``  (fullwidth)       -> 12.0
    ``'१३.01.2026'``            -> ``'2026-01-13'``   a date the system never held
    ``'१२३.०१'``                -> 12301 minor units  an amount never sent
    ``'२०२०-01-01'``            -> a per-item staleness verdict

Each of those is *silent*: no exception, no counter, no log line. The failure
mode is not a crash, it is a **plausible wrong number**, which is the worst kind
here -- an amount that is off by a debit, a posting date moved to another period,
an escalation dropped because its date "aged out". A caller cannot defend against
a value that looks like the right shape.

The rule this module enforces is deliberately narrow: **a cell that is not
written in ASCII decimal digits is not a number, a date or an amount.** It is
*unreadable*, which every caller already knows how to report -- and reporting is
strictly better than guessing, because a non-ASCII digit is overwhelmingly far
more likely to be a mangled cell than a genuine base-10 value in a Mongolian
ERP. Where the platform cannot tell, it says so; where it would have to rewrite
the operator's value to proceed, it refuses instead.

Two public helpers, and they differ on purpose:

``is_ascii_number``  asks "may we read this as a number at all?"  Used by the
                     value readers, which return ``None`` for "not a number" and
                     need to keep a *non-finite* float distinguishable from an
                     unreadable cell.
``is_ascii_digit_run`` asks "may we parse these digits?"  Used by the date and
                     amount parsers, which raise their own module error and must
                     refuse *before* ``int()`` gets a chance to normalise.
"""

from __future__ import annotations

import re

__all__ = ['is_ascii_digit_run', 'is_ascii_number', 'ASCII_NUMBER_RE']

# Anchored, full-match, and explicitly ASCII. ``[0-9]`` rather than ``\d`` is the
# entire point of this file; ``re.ASCII`` would do the same but is easy to lose in
# a later edit, whereas the character class carries its reason in the text.
ASCII_NUMBER_RE = re.compile(r'^[0-9]{1,15}(?:[.,][0-9]{1,9})?$')


def is_ascii_digit_run(text):
    """Whether ``text`` is nothing but ASCII decimal digits, and non-empty.

    For callers that have already matched a shape and are about to call ``int()``
    on the groups. Applied to the *whole* string by the date parsers (catching a
    separator that differs, too) and to each *part* by the amount parser.
    """
    if not isinstance(text, str) or not text:
        return False
    return all('0' <= character <= '9' for character in text)


def is_ascii_number(text):
    """Whether ``text`` is an ASCII decimal number this platform will read.

    Accepts an optional sign and an optional decimal part with either ``.`` or
    ``,`` as the separator, because both are written by operators in the wild.
    Rejects exponent notation outright: ``'1e5'`` is a spreadsheet's rendering of
    a large number, not necessarily the number the operator typed, and a quantity
    that silently gains three orders of magnitude is the same class of defect as
    the one this module was written to close.
    """
    if not isinstance(text, str):
        return False
    body = text.strip()
    if body.startswith(('-', '+')):
        body = body[1:]
    if not body:
        return False
    return bool(ASCII_NUMBER_RE.match(body))
