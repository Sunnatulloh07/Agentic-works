"""Run runtime_tests under verify_offline.py's network audit hook.

``scripts/verify_offline.py`` installs an audit hook that raises on ``socket.connect``,
``socket.getaddrinfo`` and ``socket.sendto``, then runs the Python half of the gate.
The full gate also builds Node and Bun surfaces and outlives a short command budget, so
this reproduces *only* the Python half, with the same hook text, over the same package.

It exists to answer one question quickly: is a new ``runtime_tests`` module socket-free?
On Windows an event loop is itself a socket -- ``ProactorEventLoop`` and
``SelectorEventLoop`` both build their self-pipe from ``socket.socketpair()`` -- so a
test that merely calls ``asyncio.run`` trips this hook.  That is the trap this catches.

Run:  python scripts/probes/run_runtime_tests_offline.py
"""

import os
import sys
import unittest
from pathlib import Path


def block(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('Offline verification: network disabled')


sys.addaudithook(block)

if __name__ == '__main__':
    # Run from api-python/: sys.path[0] is this script's own directory, so the package
    # under test would not be importable without putting the CWD first.
    root = os.getcwd()
    sys.path.insert(0, root)
    # runtime_tests is a namespace package (no __init__.py), which loader.discover
    # refuses when top_level_dir is set.  Naming the modules is the same suite.
    # Optional argv narrows it: the whole package outlives a short command budget.
    if len(sys.argv) > 1:
        names = [name if name.startswith('runtime_tests.') else 'runtime_tests.' + name
                 for name in sys.argv[1:]]
    else:
        names = sorted('runtime_tests.' + path.stem
                       for path in Path(root, 'runtime_tests').glob('test_*.py'))
    suite = unittest.TestLoader().loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
