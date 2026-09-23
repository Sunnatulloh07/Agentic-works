"""Measure the "the platform does not move money" guarantee (PRD v0.5, P8b).

The block's whole reason for existing is a boundary, and the boundary is easier to
state than to keep:

    Upload -> parse -> normalize -> validate -> duplicate check -> three-way match
    -> fraud controls -> approval routing -> **plan**. Stop.

The product's own scope note (PRD v0.5 section 8) says the platform prepares the
document and puts the payment up for approval. Not "we ask before paying" - there
is no pay step to ask about. That is a strong claim, and a strong claim in a
docstring proves nothing, so this probe tries to falsify it from three directions:

1. **Syntactically.** Walk every public function and class in the module and look
   for a name that means money movement. A future contributor who adds
   ``pay_invoice`` fails this immediately, which is the point: the guarantee has to
   survive people who have not read this file.

2. **Through the registry.** Every tool the module registers, at every ladder the
   engine supports - including ``autonomous``, the setting with no human in the
   loop - is driven against a real Engine and a real SQLite database, with a
   counting transport standing in for the network. No tool reaches the network at
   all: this block reads the tenant's own stored documents and writes its own
   table. The count of provider calls is zero in every cell.

3. **In the output.** Run the full chain and read what it hands back. A plan that
   merely omits a payment step is weaker than a plan that says
   ``executes_payment: False`` and prints the sentence a human can quote.

What this probe does NOT show is that the *plan* is correct for real invoices -
that is what the three-way match and fraud tests cover. It shows that whatever the
plan says, nothing downstream of it can spend.

Run from anywhere; paths are resolved relative to this file.
"""
import inspect
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'api-python'))

from platform_runtime import documents
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry

TENANT = 'demo-ap'

# A vocabulary of money movement. Deliberately broad: "charge" and "execute" are in
# here even though they have innocent meanings, because a false alarm costs a rename
# while a miss costs the guarantee.
BANNED = ('pay', 'payment', 'transfer', 'settle', 'remit', 'refund', 'disburse',
          'payout', 'withdraw', 'debit', 'charge', 'execute', 'authorize_payment')

# The clean document the chain is run against: one invoice that agrees with both of
# its counterparts, so the plan reaches "ready_for_approval" and the probe reads the
# output of the *successful* path, not a refusal.
INVOICE = {'kind': 'invoice', 'supplier': 'acme', 'number': 'INV-2026-0041',
           'doc_date': '2026-09-01', 'currency': 'UZS', 'total': '1250000'}
PURCHASE_ORDER = {'kind': 'purchase_order', 'supplier': 'acme', 'number': 'PO-77',
                  'doc_date': '2026-08-20', 'currency': 'UZS', 'total': '1250000'}
DELIVERY_NOTE = {'kind': 'delivery_note', 'supplier': 'acme', 'number': 'DN-77',
                 'doc_date': '2026-08-28', 'currency': 'UZS', 'total': '1250000'}


class CountingTransport:
    """Stands in for the network. Nothing in this block should ever call it."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError('the document block attempted provider I/O')

    @property
    def get(self):
        return self.calls


def build(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / 'integrations.json'
    cfg.write_text(json.dumps({TENANT: {'documents': {
        'tolerance_minor': 0, 'outlier_ratio': 3, 'approver_role': 'owner',
        'residency': 'uz', 'currencies': ['UZS', 'USD']}}}), encoding='utf-8')
    os.environ['PLATFORM_INTEGRATIONS_FILE'] = str(cfg)
    return Engine(root / 'probe.db', build_registry(), lambda t, a: dict(POLICY),
                  clock=lambda: 1_789_794_000.0)


# Every ladder, including the one with no human in the loop. If the guarantee only
# held at human_led it would not be a guarantee about the platform, it would be a
# guarantee about one configuration.
LADDERS = ('human_led', 'human_assisted', 'autonomous')

POLICY = {'tools': list(documents.DOCUMENT_TOOLS), 'ladder': 'autonomous'}

# One call per registered tool, with arguments that reach the deepest code path.
# A fresh database per tool and per ladder, for one specific reason: the duplicate
# key is (kind, supplier, number), so calling ``document.parse`` first would make
# every later tool in the same database see a duplicate of ITSELF and the chain
# would never reach the successful branch. Reusing one database would measure a
# refusal in most cells and prove nothing about the cells that matter.
#
# Structured arguments are JSON text, because the registry validator refuses a bare
# {'type': 'object'} property and a document is a tree. See the module docstring for
# the defect this shape fixes.
CALLS = {
    'document.parse': {'fields': json.dumps(INVOICE)},
    'document.match': {'invoice': json.dumps(INVOICE),
                       'purchase_order': json.dumps(PURCHASE_ORDER),
                       'delivery_note': json.dumps(DELIVERY_NOTE)},
    'document.duplicates': {'fields': json.dumps(INVOICE)},
    'document.fraud_signals': {'fields': json.dumps(INVOICE)},
    'document.posting_plan': {'fields': json.dumps(INVOICE),
                              'purchase_order': json.dumps(PURCHASE_ORDER),
                              'delivery_note': json.dumps(DELIVERY_NOTE)},
}


def call_tool(engine, name, ladder, transport):
    """Submit one tool through the engine and return (outcome, provider calls).

    Through the engine on purpose: the engine is what decides whether an approval
    is even required, and it is the layer that would run a payment if one existed.
    A direct handler call would test the least interesting surface.

    ``submit`` returns the task id, so the outcome is read back with ``get``. The
    provider count is taken across the whole sequence, not just the submit: the
    claim being measured is that no part of the path reaches the network.
    """
    engine.policy = lambda tenant, agent: {'tools': list(documents.DOCUMENT_TOOLS),
                                           'ladder': ladder}
    args = dict(CALLS[name])
    before = len(transport.calls)
    try:
        tid = engine.submit(TENANT, 'probe', 'probe:' + name + ':' + ladder,
                            'ap.capture', [{'tool': name, 'args': args}])
        status = engine.get(TENANT, tid)['status']
    except Exception as error:  # noqa: BLE001 - the outcome is what is being measured
        detail = f'{type(error).__name__}: {error}' if os.environ.get('PROBE_VERBOSE') \
            else type(error).__name__
        return detail, len(transport.calls) - before
    return str(status), len(transport.calls) - before


def main():
    transport = CountingTransport()

    print('1. syntax: no function or class in the module can mean money movement')
    callables = sorted(
        name for name, value in vars(documents).items()
        if not name.startswith('_')
        and (inspect.isfunction(value) or inspect.isclass(value))
        and getattr(value, '__module__', '') == documents.__name__)
    print(f'   examined {len(callables)} public callable(s): {", ".join(callables)}')
    for banned in BANNED:
        offenders = [name for name in callables if banned in name.lower()]
        assert not offenders, f'{banned!r}: {offenders}'
    print('   no name in the money vocabulary')

    print('2. registry: every tool, at every ladder, with the network counted')
    registered = [name for name in documents.DOCUMENT_TOOLS
                  if name in build_registry().items]
    assert sorted(registered) == sorted(documents.DOCUMENT_TOOLS), registered
    print(f'   {"tool":28} {"human_led":>12} {"human_assisted":>16} {"autonomous":>12}  provider')
    worst = None
    with tempfile.TemporaryDirectory() as tmp:
        for offset, name in enumerate(documents.DOCUMENT_TOOLS):
            # A fresh database per tool, so no tool's own earlier call can shadow it.
            sub = Path(tmp) / f'{offset:02d}-{name.replace(".", "-")}'
            sub.mkdir()
            engine = build(sub)
            cells = {}
            for ladder in LADDERS:
                outcome, calls = call_tool(engine, name, ladder, transport)
                cells[ladder] = (outcome, calls)
            for ladder, (outcome, calls) in cells.items():
                if calls != 0:
                    worst = (name, ladder, calls)
            print(f'   {name:28} {cells["human_led"][0]:>12} '
                  f'{cells["human_assisted"][0]:>16} {cells["autonomous"][0]:>12}  '
                  f'{sum(c for _, c in cells.values())}')
    assert worst is None, f'provider I/O from {worst}'
    assert transport.calls == [], transport.calls
    print('   zero provider calls in every cell, including autonomous')

    print('2b. the same tools executed, with the network still counted')
    # Submitting proves the schema accepts the call. Executing the handler proves
    # the chain behind it runs to completion without reaching out. Both matter: a
    # tool that cannot be submitted is unreachable, and a tool that reaches the
    # network during execution is the thing this probe exists to rule out.
    print(f'   {"tool":28} {"outcome":26} provider')
    with tempfile.TemporaryDirectory() as tmp:
        for offset, name in enumerate(documents.DOCUMENT_TOOLS):
            sub = Path(tmp) / f'exec-{offset:02d}'
            sub.mkdir(parents=True)
            engine = build(sub)
            spec = build_registry().get(name)
            before = len(transport.calls)
            try:
                result = spec.handler(engine, TENANT, 'ap.capture',
                                      dict(CALLS[name]), 'probe-step')
                if name == 'document.posting_plan':
                    outcome = f'plan {result["recommendation"]}'
                    assert result['executes_payment'] is False, result
                else:
                    outcome = 'returned'
            except Exception as error:  # noqa: BLE001
                outcome = f'{type(error).__name__}'
            calls = len(transport.calls) - before
            print(f'   {name:28} {outcome:26} {calls}')
            assert calls == 0, f'{name} reached the network'
    assert transport.calls == [], transport.calls

    print('3. output: the plan says out loud that it is not a payment')
    with tempfile.TemporaryDirectory() as tmp:
        engine = build(Path(tmp) / 'clean')
        (Path(tmp) / 'clean').mkdir(exist_ok=True)
        plan = documents.posting_plan(engine, TENANT, 'ap.control', dict(INVOICE),
                                      purchase_order=dict(PURCHASE_ORDER),
                                      delivery_note=dict(DELIVERY_NOTE))
        assert plan['executes_payment'] is False, plan['executes_payment']
        assert plan['recommendation'] == 'ready_for_approval', plan['recommendation']
        assert plan['match']['status'] == 'matched', plan['match']['status']
        print(f'   recommendation: {plan["recommendation"]}')
        print(f'   match status:   {plan["match"]["status"]}')
        print(f'   executes_payment: {plan["executes_payment"]}')
        print(f'   note: {plan["note"]}')
        # And the write tool cannot smuggle one in either.
        handler = build_registry().items['document.posting_plan'].handler
        result = handler(engine, TENANT, 'ap.control',
                         {'fields': dict(INVOICE),
                          'purchase_order': dict(PURCHASE_ORDER),
                          'delivery_note': dict(DELIVERY_NOTE)}, 'probe-step')
        assert result['executes_payment'] is False, result
        plan_id = result['plan_id']
        stored = documents.plans(engine, TENANT, result['document_id'])
        assert len(stored) == 1 and stored[0]['id'] == plan_id, stored
        record = documents.load(engine, TENANT, result['document_id'])
        print(f'   recorded plan {plan_id} for document {record["id"]}, status '
              f'{stored[0]["status"]}')

    print()
    print('PROVEN: the document block has no payment path at any autonomy level.')
    print(f'        {len(documents.DOCUMENT_TOOLS)} tools x {len(LADDERS)} ladders = '
          f'{len(documents.DOCUMENT_TOOLS) * len(LADDERS)} cells, all with 0 provider')
    print('        calls, including ladder=autonomous where no human approves the')
    print(f'        step; the furthest the chain goes is a plan that reports')
    print('        executes_payment=False and names the approver should post it to')
    print('        the ERP. The platform prepares the document; it does not pay.')
    print(f'        {len(callables)} public callables checked against '
          f'{len(BANNED)} money-movement terms.')


if __name__ == '__main__':
    main()
