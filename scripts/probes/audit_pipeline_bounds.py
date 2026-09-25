"""Boundary audit of ``app/pipeline.py`` — the revert matrix.

``pipeline.py`` is the one door every inbound channel walks through, and it had
**no runtime test file at all** before ``runtime_tests/test_pipeline_bounds.py``:
its behaviour was covered only from the dependency-backed suite, which this
instrument cannot run. Every bound was also inline — ``4000`` twice, ``1..99``
(a third copy of a range the Order model and the approval-time re-check each
carried), ``200``, ``500``, ``8``, ``10 ** 9``, a bare ``998`` inside a pattern —
so nothing could address them by name, and nothing would have failed if one
widened.

The spec is one file, plainly: the pins live in ``test_pipeline_bounds``. The
legacy happy path is NOT covered there (it needs a pack directory, an approvals
store and a quota table), so the last mutation below is kept to make that visible
in the measurement: renaming ``STOP_COMMAND`` is caught by the literal constant
pin, NOT by legacy behaviour. Measured run: 17/17 RED, restore verified.

Run from the repository root::

    python scripts/probes/audit_pipeline_bounds.py --verify   # patterns only
    python scripts/probes/audit_pipeline_bounds.py            # full matrix
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revert_matrix

TARGET = 'app/pipeline.py'
PATTERN = 'runtime_tests.test_pipeline_bounds'

BS = bytes([92])  # the regex backslash, kept out of this file's source text

MUTATIONS = [
    ('buy command renamed',
     b'BUY_COMMAND = "/buy"',
     b'BUY_COMMAND = "/buyy"'),
    ('text ceiling 4000 -> 40000',
     b'MAX_TEXT_CHARS = 4000',
     b'MAX_TEXT_CHARS = 40000'),
    ('customer ceiling 200 -> 2000',
     b'MAX_CUSTOMER_CHARS = 200',
     b'MAX_CUSTOMER_CHARS = 2000'),
    ('lead ceiling 500 -> 5000',
     b'MAX_LEAD_CHARS = 500',
     b'MAX_LEAD_CHARS = 5000'),
    ('stable id slice 8 -> 16',
     b'STABLE_ID_HEX_CHARS = 8',
     b'STABLE_ID_HEX_CHARS = 16'),
    ('stable id modulus 10**9 -> 10**12',
     b'STABLE_ID_MODULUS = 10 ** 9',
     b'STABLE_ID_MODULUS = 10 ** 12'),
    ('country code 998 -> 999',
     b'UZ_COUNTRY_CODE = "998"',
     b'UZ_COUNTRY_CODE = "999"'),
    ('national digits 9 -> 10',
     b'UZ_NATIONAL_DIGITS = 9',
     b'UZ_NATIONAL_DIGITS = 10'),
    ('phone cleaner drops hyphens and parentheses',
     b'PHONE_CLEAN = re.compile(r"[' + BS + b's' + BS + b'-()]")',
     b'PHONE_CLEAN = re.compile(r"[' + BS + b's]")'),
    ('quantity guard removed',
     b'if qty < MIN_QUANTITY or qty > MAX_QUANTITY:',
     b'if False:'),
    ('quantity message loses its numbers',
     b'detail=f"Son {MIN_QUANTITY}-{MAX_QUANTITY} oralig' + bytes([39]) + b'ida bo' + bytes([39]) + b'lsin")',
     b'detail="Son noto' + bytes([39]) + b'g' + bytes([39]) + b'ri")'),
    ('customer name cap removed',
     b'customer=" ".join(rest)[:MAX_CUSTOMER_CHARS] or "mijoz",',
     b'customer=" ".join(rest) or "mijoz",'),
    ('ui channel mapping removed',
     b'if channel == "ui": channel = "web"',
     b'if False: channel = "web"'),
    ('platform text truncation removed',
     b'"text":(text or "")[:MAX_TEXT_CHARS]})',
     b'"text":(text or "")})'),
    ('legacy production refusal removed',
     b'if is_prod():',
     b'if False:'),
    ('duplicate key no longer absorbed',
     b'except sqlite3.IntegrityError:',
     b'except ValueError:'),
    # Constanta pin'i tutadi, LEGACY XULQI emas: /stop faqat legacy yo'lda
    # o'qiladi va bu spec o'sha yo'lni qoplamaydi. Bu qator shu bo'shliqni
    # ko'rinadigan qilish uchun qoldirilgan.
    ('stop command renamed (caught by the constant pin, not by legacy behaviour)',
     b'STOP_COMMAND = "/stop"',
     b'STOP_COMMAND = "/stopx"'),
]

if __name__ == '__main__':
    if '--verify' in sys.argv:
        sys.exit(revert_matrix.verify(TARGET, MUTATIONS))
    sys.exit(revert_matrix.main(TARGET, PATTERN, MUTATIONS, revert_matrix.AUTO_BASELINE))
