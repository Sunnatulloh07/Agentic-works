"""Measure how much of the runtime a delivered pack can actually reach.

``README.md`` and ``docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md`` both lead
with a coverage claim -- the registry holds 91 tools and 20 of them are
reachable, and 83.8% of the tool-owning ``platform_runtime`` modules cannot be
reached. Until this script existed neither number could be re-derived: each was
measured by hand in one session, published, and the method left with the
session. That is not a hypothetical risk in this repository. §161 found that the
two earlier figures (12/89 and 17/88) had been measured against **bare**
``build_registry()``, which never registers the three host-injected shop tools
-- so the reachable set omitted tools that ARE reachable and the denominator was
wrong as well. The unreachable-LOC figure had the same disease in a different
organ: §161 published 54% from a hand-picked module list, and this probe
replaced it with a stated rule. Both numbers moved when the method was
corrected, and nothing in the tree recorded either correction as executable code
until now.

A headline number with no committed measurement is an assertion wearing a
number's clothes, and the inventory's own opening line promises measurement
rather than estimate. This is that measurement.

Run it::

    python scripts/probes/measure_reachability.py
    python scripts/probes/measure_reachability.py --json

Exit 1 means one of three defects, and none of them is a coverage percentage: a
pack refused to load, a declared name is absent from ``known_tool_names()`` (the
two guards disagreeing), or a declared name is absent from the engine registry
the host actually builds. A low coverage figure is an honest measurement and is
never a failure.

``--json`` writes exactly one JSON document to stdout, nothing to stderr, and
carries its own verdict in ``verdict`` and ``failures`` fields. That is not a
stylistic choice: the offline gate captures every job with
``stderr=subprocess.STDOUT`` and stores the merged text as evidence, so anything
written beside the document lands inside the record and stops it parsing -- while
the probe and the gate both still report PASS.

METHOD, stated explicitly because the method is the part that was wrong before
------------------------------------------------------------------------------
Denominator   ``build_registry(catalog, shop_data)``: the registry
              ``app.platform_api.engine()`` actually constructs. Bare
              ``build_registry()`` is NOT that registry -- see above.
Reachable     the union of every ``agent.tools`` entry across every directory
              under ``packs/``, intersected with the registry above.
Shipped       a directory under ``packs/`` whose name does not start with ``_``.
              ``_template`` is a scaffold a new customer copies -- its own YAML
              says "namuna, nusxalab o'zgartiring" -- and no tenant is served
              from it, so it is NOT in the numerator. It is still loaded and
              still validated, because a template declaring a tool no adapter
              provides propagates that error into every pack derived from it,
              and its own contribution is reported separately so that a future
              template-only tool is visible instead of being silently folded
              into a number that claims to describe delivered packs. Today the
              two readings agree (20 either way) only because every tool the
              template declares is also declared by ``demo-retail``. That is
              luck, not a property, and the separation is what keeps it a fact.
              One caveat, stated rather than buried: ``load_pack`` maps the name
              "template" onto ``packs/_template`` by an explicit special case, so
              the scaffold IS loadable under that name and a workspace literally
              called "template" would be served it. Every call site passes a
              tenant identity, so that is a naming accident rather than an
              onboarding path and it does not make the scaffold a delivered pack
              -- but it is the one place this exclusion can be argued with, so it
              is written down instead of left to be rediscovered.
Hollow        a declared name absent from ``known_tool_names()``. MUST stay
              empty, and it is worth being precise about why it is empty: this
              is a **guard-divergence invariant, not a measurement of the
              packs**. ``load_pack`` refuses a pack whose agent declares an
              unknown tool -- ``unknown_tools()`` is literally
              ``name not in known_tool_names()`` -- so a pack that gets past it
              cannot contribute an unknown name, and this set is empty by
              construction. A non-zero figure therefore does not mean a pack is
              broken; it means ``load_pack``'s check and ``known_tool_names()``
              have stopped agreeing, which is a defect in the guard. Reporting
              it as "0 hollow claims in the packs" would claim credit for
              somebody else's defence, so it is labelled as what it is. The
              probe still catches the ``PackError`` ``load_pack`` raises and
              reports it instead of dying on a traceback, naming the pack, the
              agent and the tool.
Registry gap  a declared name absent from the ENGINE registry. This is the check
              ``load_pack`` structurally cannot make, and so the only one here
              that measures something the rest of the tree does not: it
              validates against the full catalog-conditional name set, while
              this compares against the registry the host actually builds. The
              two coincide today because both hold 91 names, but they separate
              the moment a host stops injecting its catalog or shop reader --
              verified, not assumed: ``build_registry(None, shop_data)`` drops
              ``products.search`` and ``build_registry(catalog, None)`` drops
              ``orders.draft`` and ``shop.info``, while every pack still loads,
              because ``load_pack`` consults the stub-built name set. The pack
              would ship, the tool would simply not be there at runtime. That
              is §161's measurement error turned into a permanent gate.
Attribution   ``spec.handler.__module__``. ``fs.list`` and ``fs.read_text`` are
              runner-executed and carry no in-process handler, so they are
              attributed to ``platform_runtime.tools``, which registers them.
LOC           the universe is modules that own at least one registry tool, and
              such a module is unreachable when none of its tools is reachable.
              Restricting the universe is what makes the number mean something.
              Score every top-level module instead and ``engine.py``, ``llm.py``
              and ``conversation.py`` land in the unreachable bucket: they own no
              tool, but they are reached through API routes, webhooks and the
              agent loop, and they are the most-used code in the system. That
              mistake reports 88.7% and calls the engine dead. The ``package``
              figure applies the same rule over every ``.py`` under the package,
              which adds ``crm/`` and ``database/``.

              CORRECTION. §161 published "unreachable LOC: 54%, 14 modules,
              10 447 / 19 182 lines". 19 182 is real -- it is exactly the line
              count of ``platform_runtime/*.py`` -- but no stated rule reproduces
              the numerator. Measured here, the two sound readings are 83.8%
              (11 283 / 13 460, 17 of 22 top-level tool modules) and 84.5%
              (11 828 / 14 005, 19 of 24 package-wide). Only five top-level
              modules own a reachable tool at all: ``knowledge``, ``oversight``,
              ``shop_tools``, ``tools`` and ``whatsapp``. The published 54% was
              assembled from a hand-picked module list, which is the same failure
              mode §161 was itself correcting when it found ``oversight`` had
              been left off that list. Quote this probe, not that number.

The probe is read-only: it builds a registry and reads source files. It never
serves traffic, opens a database or calls a provider. It refuses to run under a
production ENV rather than quietly borrowing production configuration in order
to count lines.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = REPO / 'api-python'
PACKAGE = API / 'platform_runtime'

# Registered without an in-process handler: the runner executes them, so
# ``spec.handler`` is None and attribution would otherwise lose two tools.
RUNNER_OWNER = 'platform_runtime.tools'

# A directory under ``packs/`` whose name starts with this is a scaffold, not a
# delivered pack. ``_template`` holds a ``pack.yaml`` full of "namuna, nusxalab
# o'zgartiring" and no tenant is served from it, so counting its tools as
# reachable would let the headline rise without any customer receiving anything.
TEMPLATE_PREFIX = '_'


def prepare_environment():
    """Make ``app`` importable without borrowing production configuration.

    ``app.platform_api`` imports ``app.auth``, which validates the runtime
    configuration at import time, so a valid ENV has to exist before the probe
    can read a single pack. ``ENV=test`` with ``ALLOW_INSECURE_DEV`` takes the
    documented local defaults and needs no secrets. A production ENV is refused
    rather than used: a line-counting probe has no reason to load the
    configuration the platform would serve real traffic with, and inheriting it
    silently would make the probe's safety depend on the shell it happened to
    run in. Existing non-production values are left alone.
    """
    environment = os.environ.get('ENV', '').lower().strip()
    if environment in {'prod', 'production'}:
        raise SystemExit(
            'REFUSED: ENV=%s. This probe only measures coverage. Re-run it with\n'
            'ENV=test so it cannot load production configuration.' % environment)
    os.environ.setdefault('ENV', 'test')
    os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')
    return os.environ['ENV']


def module_of(path):
    """Dotted module name for a source file under ``platform_runtime``.

    Matches the ``__module__`` string a handler reports, which is what makes the
    attribution join work: ``platform_runtime/crm/crm_gateway.py`` becomes
    ``platform_runtime.crm.crm_gateway``.
    """
    relative = path.relative_to(PACKAGE).with_suffix('')
    return 'platform_runtime.' + relative.as_posix().replace('/', '.')


def line_count(path):
    return len(path.read_text(encoding='utf-8').splitlines())


def measure():
    """Return every figure this probe publishes, as plain data."""
    prepare_environment()
    sys.path.insert(0, str(API))
    from app.packs import PACKS_DIR, PackError, load_pack
    from app.platform_api import catalog, shop_data
    from platform_runtime.tools import build_registry, known_tool_names

    # The engine's own registry, not the bare one. Passing the host's catalog and
    # shop readers is what registers products.search, shop.info and orders.draft;
    # omitting them is the measurement error §161 corrected.
    registry = build_registry(catalog, shop_data)
    registered = set(registry.items)

    pack_names = sorted(p.name for p in PACKS_DIR.iterdir() if p.is_dir())
    shipped = [name for name in pack_names if not name.startswith(TEMPLATE_PREFIX)]
    templates = [name for name in pack_names if name.startswith(TEMPLATE_PREFIX)]
    declared, template_declared, per_pack, load_failures = set(), set(), {}, []
    for pack_name in pack_names:
        try:
            pack = load_pack(pack_name)
        except PackError as exc:
            # ``load_pack`` is the FIRST defence against a hollow claim: it refuses a
            # pack whose agent declares a tool no adapter provides, so a pack that
            # gets past it cannot declare an unknown tool. Catching here keeps this
            # probe's failure actionable -- the gate then reports a message instead of
            # a traceback, which is the difference between a red you act on and a red
            # you have to go and read a stack trace to understand.
            load_failures.append({'pack': pack_name, 'error': str(exc)})
            continue
        tools = {tool for agent in pack.agents for tool in agent.tools}
        per_pack[pack_name] = sorted(tools)
        # Every directory is loaded and validated -- a template declaring a tool no
        # adapter provides would propagate that error into every pack copied from it
        # -- but only a shipped pack contributes to the headline. A template is what
        # a future customer will copy, not what a present one can call.
        if pack_name.startswith(TEMPLATE_PREFIX):
            template_declared |= tools
        else:
            declared |= tools

    known = set(known_tool_names())
    reachable = declared & registered
    # The two guard checks cover every pack that loaded, template included: a
    # scaffold declaring a tool nobody implements teaches that mistake to every pack
    # copied from it, which is worse than one broken pack. Coverage itself stays
    # shipped-only -- that is the whole point of separating the two sets.
    all_declared = declared | template_declared

    owner = defaultdict(list)
    for name, spec in registry.items.items():
        module = getattr(spec.handler, '__module__', '') or RUNNER_OWNER
        owner[module].append(name)

    def loc(paths):
        return sum(line_count(path) for path in paths)

    def classify(paths):
        """Split by whether a module owns a tool, then by reachability.

        Only tool-owning modules belong in the denominator. A module that owns no
        tool -- ``engine.py``, ``llm.py``, ``conversation.py`` -- is reached
        through API routes, webhooks and the agent loop rather than through a
        pack's tool surface, so scoring it as unreachable counts the most-used
        code in the system as dead. Including those modules is easy to do by
        accident and inflates the figure to 88.7%, answering a question nobody
        asked. They are counted separately here so the mistake is visible rather
        than baked into the headline.
        """
        owning, core = [], []
        for path in paths:
            (owning if owner.get(module_of(path)) else core).append(path)
        reached = [p for p in owning if set(owner[module_of(p)]) & reachable]
        unreached = [p for p in owning if not set(owner[module_of(p)]) & reachable]
        return reached, unreached, core

    def block(reached, unreached, core):
        total = loc(reached) + loc(unreached)
        return {'owning_modules': len(reached) + len(unreached),
                'reachable_modules': len(reached),
                'unreachable_modules': len(unreached),
                'unreachable_lines': loc(unreached),
                'owning_lines': total,
                'percent': round(100.0 * loc(unreached) / total, 1) if total else 0.0,
                'core_modules_owning_no_tool': len(core),
                'core_lines': loc(core)}

    top = classify(sorted(PACKAGE.glob('*.py')))
    allpy = classify(sorted(PACKAGE.rglob('*.py')))
    return {
        'registry_tools': len(registered),
        'known_tool_names': len(known),
        'packs': pack_names,
        'shipped_packs': shipped,
        'template_packs': templates,
        'declared_tools': len(declared),
        'template_declared_tools': len(template_declared),
        # Empty today, and reported rather than assumed. A tool only a template
        # declares is not reachable by any customer, so if this ever becomes
        # non-empty the headline and the scaffold have diverged -- and the
        # difference has to be visible instead of folded into a number whose label
        # says "reachable from a delivered pack".
        'template_only_tools': sorted(template_declared - declared),
        'reachable_tools': len(reachable),
        'reachable': sorted(reachable),
        'hollow_claims': sorted(all_declared - known),
        # Distinct from hollow_claims, and the check ``load_pack`` cannot make: it
        # validates against ``known_tool_names()``, the full catalog-conditional set,
        # while this compares against the registry the engine actually builds. The two
        # coincide today because both hold 91 names, but a host that stopped injecting
        # its catalog or shop reader would separate them -- the pack would still load
        # and the tool would simply not be there at runtime.
        'not_in_engine_registry': sorted(all_declared - registered),
        'load_failures': load_failures,
        'declared_per_pack': per_pack,
        'loc': {'tool_modules': block(*top), 'package': block(*allpy)},
        'reachable_tool_modules': [module_of(p) for p in top[0]],
        'unreachable_tool_modules': [module_of(p) for p in top[1]],
    }


def report(result):
    """Print the measurement the way the docs quote it, with the method attached.

    The per-pack lines are printed because the union alone hides which pack
    contributes what -- §161's correction was exactly a case where one pack's
    three tools had been left out of the count.
    """
    loc = result['loc']
    print('=== coverage: what a delivered pack can actually reach ===')
    print('registry (engine registry, build_registry(catalog, shop_data)): %d tools'
          % result['registry_tools'])
    print('known_tool_names() (catalog-conditional names included):         %d'
          % result['known_tool_names'])
    print('shipped packs: %s' % ', '.join(result['shipped_packs']))
    print('templates (loaded and validated, NOT counted as shipped): %s'
          % (', '.join(result['template_packs']) or 'none'))
    print()
    for pack_name in result['packs']:
        # A pack that refused to load has no entry, and saying so here is the point:
        # the alternative is a KeyError that hides the real message behind a stack.
        declared_tools = result['declared_per_pack'].get(pack_name)
        counted = '' if pack_name in result['shipped_packs'] else '   [template]'
        if declared_tools is None:
            print('  %-14s REFUSED TO LOAD (see failures below)%s' % (pack_name, counted))
        else:
            print('  %-14s declares %2d tools%s'
                  % (pack_name, len(declared_tools), counted))
    print()
    print('REACHABLE: %d / %d' % (result['reachable_tools'], result['registry_tools']))
    print('  %s' % ', '.join(result['reachable']))
    print()
    print('TEMPLATE-ONLY TOOLS (a scaffold declares them, no shipped pack does): %d'
          % len(result['template_only_tools']))
    for name in result['template_only_tools']:
        print('  %s' % name)
    print()
    print('PACK LOAD FAILURES: %d' % len(result['load_failures']))
    for failure in result['load_failures']:
        print('  %s: %s' % (failure['pack'], failure['error']))
    # Not a measurement of the packs. ``load_pack`` already refuses a pack whose
    # agent declares an unknown tool, so this set is empty by construction, and a
    # non-zero figure means the two guards have stopped agreeing rather than that a
    # pack is broken. Labelling it "hollow claims in the packs" would claim credit
    # for somebody else's defence, so it is labelled as what it actually is.
    print('GUARD DIVERGENCE (declared, yet absent from known_tool_names()): %d'
          % len(result['hollow_claims']))
    for name in result['hollow_claims']:
        print('  %s' % name)
    print('DECLARED BUT ABSENT FROM THE ENGINE REGISTRY: %d'
          % len(result['not_in_engine_registry']))
    for name in result['not_in_engine_registry']:
        print('  %s' % name)
    print()
    print('=== unreachable LOC, among modules that own a registry tool ===')
    for label, title in (('tool_modules', 'platform_runtime/*.py owning >=1 tool'),
                         ('package', 'same rule over every .py under the package')):
        row = loc[label]
        print('%-13s %6d / %6d lines = %5.1f%%   (%d of %d tool-owning modules)   %s'
              % (label, row['unreachable_lines'], row['owning_lines'], row['percent'],
                 row['unreachable_modules'], row['owning_modules'], title))
    print()
    print('  Excluded on purpose: %d top-level modules own no tool (%d lines). They are'
          % (loc['tool_modules']['core_modules_owning_no_tool'],
             loc['tool_modules']['core_lines']))
    print('  reached through API routes, webhooks and the agent loop, not through a')
    print('  pack tool. Scoring them unreachable reports 88.7% and calls engine.py')
    print('  dead code, so they are counted here instead of hidden.')
    print()
    print('reachable tool modules (%d): %s'
          % (len(result['reachable_tool_modules']), ', '.join(result['reachable_tool_modules'])))
    print()
    print('unreachable tool modules (%d):' % loc['tool_modules']['unreachable_modules'])
    for module in result['unreachable_tool_modules']:
        print('  %s' % module)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', action='store_true', help='emit the measurement as JSON')
    args = parser.parse_args()
    result = measure()
    # Three distinct defects, each actionable. None of them is a coverage
    # percentage: a low number is an honest measurement, not a failure.
    failures = list(result['load_failures'])
    failures += [{'pack': 'guard divergence',
                  'error': 'declared, yet absent from known_tool_names(): load_pack '
                           'and the name set disagree about %s' % name}
                 for name in result['hollow_claims']]
    failures += [{'pack': 'engine registry',
                  'error': 'declared by a pack that still loads, but absent from the '
                           'registry the host builds: %s' % name}
                 for name in result['not_in_engine_registry']]
    if args.json:
        # ``--json`` is the mode the offline gate runs, and the gate captures every
        # job with ``stderr=subprocess.STDOUT``: the two streams are merged into one
        # <name>.log. Merging is load-bearing there -- unittest writes its FAIL and
        # ERROR lines to stderr and the recorded-blocked comparison reads them -- so
        # the probe has to be the side that stays clean. It cannot be clean by
        # separating the streams either: stdout is block-buffered while piped and
        # stderr is not, so a verdict on stderr arrives FIRST and the evidence file
        # the gate keeps starts with prose and stops parsing as JSON. That is worse
        # than the defect it replaced -- the probe reports PASS, the gate reports
        # PASS, and the record neither of them can read. The verdict therefore
        # travels inside the document, as data rather than as a line beside it.
        document = dict(result)
        document['verdict'] = 'FAIL' if failures else 'PASS'
        document['failures'] = failures
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 1 if failures else 0
    report(result)
    for failure in failures:
        print('FAILED: %s -- %s' % (failure['pack'], failure['error']))
    if failures:
        return 1
    print('OK: every tool a shipped pack declares is in the engine registry the host '
          'builds, and load_pack agrees with known_tool_names()')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
