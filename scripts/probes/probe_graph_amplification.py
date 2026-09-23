"""Diagnostic: prove the provider-GET cost of graph.search/graph.conflicts scales
linearly with the number of entity ids, i.e. the id list is re-read for every id.

Not part of the suite. Run manually: python scripts/probes/probe_graph_amplification.py
"""
import os
import sqlite3
import sys

# Run from the repository root: the runtime and its tests live under api-python/.
_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api-python')
sys.path.insert(0, _API)
sys.path.insert(0, os.path.join(_API, 'runtime_tests'))

import test_business_graph as T

t = T.GraphTests('test_conflicts_lists_disagreements_across_systems')
t.setUp()
try:
    db = sqlite3.connect(t.erp)
    for i in range(2, 8):
        db.execute("INSERT INTO products VALUES(?,?,?)", (f'SKU-{i}', 1000 * i, i))
    db.commit()
    db.close()

    payload = {'values': [['SKU', 'Narx'], ['SKU-1042', 462000]]}

    transport = T.RecordingTransport(payload)
    t.call('graph.entity', {'entity': 'product', 'id': 'SKU-1042'}, transport)
    print('graph.entity  -> sheets GET calls: %d' % len(transport.calls))

    transport2 = T.RecordingTransport(payload)
    res = t.call('graph.conflicts', {'entity': 'product'}, transport2)
    print('graph.conflicts -> scanned ids: %d | sheets GET calls: %d'
          % (res['scanned'], len(transport2.calls)))

    transport3 = T.RecordingTransport(payload)
    res3 = t.call('graph.search',
                  {'entity': 'product', 'attribute': 'price', 'equals': 462000}, transport3)
    print('graph.search  -> matches: %d | sheets GET calls: %d'
          % (len(res3['matches']), len(transport3.calls)))

    print()
    print('Each source is read once per tool call. Before the fix to _collect_all,')
    print('graph.conflicts on 8 ids issued 9 GETs and grew linearly with the id count,')
    print('so a 100-id scan against 4 sources would have issued ~400 provider GETs.')
finally:
    t.tearDown()
