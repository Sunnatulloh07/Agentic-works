"""Measure how much of the identity-related test time is the password KDF.

``identity_store`` hashes with ``hashlib.scrypt(n=16384, r=8, p=1)``, which costs
about 204 ms per call on this machine. This runs the identity-adjacent test files
with a cheap-but-real scrypt, counts the calls, and prints what the KDF cost. The
suite is not modified: the patch lives in this process only.
"""
import hashlib
import os
import sys
import time
import unittest

calls = [0]
real = hashlib.scrypt


def cheap(password, salt, n=16384, r=8, p=1, dklen=32, **kw):
    calls[0] += 1
    return real(password, salt, n=2, r=1, p=1, dklen=dklen)


hashlib.scrypt = cheap

files = sys.argv[1:]
devnull = open(os.devnull, 'w')
total = 0.0
for name in files:
    start = time.perf_counter()
    suite = unittest.TestLoader().discover(
        'runtime_tests', pattern=name, top_level_dir='runtime_tests')
    unittest.TextTestRunner(verbosity=0, stream=devnull).run(suite)
    elapsed = time.perf_counter() - start
    total += elapsed
    print(f'  {elapsed:6.1f}s  {name}')

print()
print(f'patched total        : {total:.1f}s')
print(f'scrypt calls         : {calls[0]}')
print(f'KDF cost at 204ms    : {calls[0] * 0.204:.1f}s')
