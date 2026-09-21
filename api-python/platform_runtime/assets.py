"""The customer's equipment, addressed by path (PRD v0.5, P11 / T4).

A factory asks questions that start with *where*: which line is behind, which
station produced this reject, what is downstream of the press. Answering those
needs an identity for the equipment, and the identity has to be stable when the
SCADA behind it is replaced.

The rule this module follows
----------------------------

    "start with the asset model, not the tags"  — PRD v0.5 §2.3 (UNS)

A tag is a PLC address. If camera events were bound to tags, then replacing one
PLC would orphan every event ever recorded against it, and the same physical
machine would appear under two identities across a maintenance window. So the
identity here is a **hierarchical path**:

    zavod-1/sex-2/liniya-3/stanok-7

The path is the identity. It is declared by the operator (``levels``), stored as
the ``identity`` attribute of an ``asset`` entity in the Business Graph, and
matched literally against what the customer's systems already return.

What this module is not
-----------------------

* **It does not read SCADA.** It reads the asset register through the same
  operator-declared sources the Business Graph uses — a Sheets register, a SQL
  view, an HTTP endpoint. If the factory has a UNS, we subscribe to or read it;
  we never build or own it. The platform is an OT *reader*, not an OT vendor.
* **It does not manage cameras.** That is P11b. This module only makes sure that
  when a camera event arrives, there is an asset for it to bind to.
* **It does not write.** There is no write path in this file. The asset register
  stays the customer's, because the moment we can edit the model we become the
  author of the identity that every event depends on.

Two things are enforced in code rather than assumed
---------------------------------------------------

**The path shape is validated.** A declared ``levels`` tuple fixes the depth and
the level names, so ``zavod-1/sex-2/stanok-7`` in a four-level model is refused
rather than silently accepted as a different machine. A malformed path that got
in would make two distinct assets collide.

**A path cannot escape its tree.** ``descendants`` of ``zavod-1`` returns that
plant's subtree and nothing else. The comparison is by *segments*, never by
string prefix: ``zavod-1/sex-2`` must not be treated as an ancestor of
``zavod-10/sex-2``, which a naive ``startswith`` would do and thereby leak one
plant's assets into another's view.
"""
from __future__ import annotations

import re

from . import business_graph
from .engine import Forbidden, NotFound

ASSET_TOOLS = ('asset.tree', 'asset.children', 'asset.descendants', 'asset.resolve',
               'asset.levels')

# A path segment: what a customer can reasonably type in a spreadsheet. No slash,
# no whitespace, no percent-encoding. Deliberately conservative, because a segment
# that contains a separator would make the path ambiguous.
SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')

MAX_LEVELS = 8
MAX_PATH_CHARS = 512
MAX_CHILDREN = 200
MAX_DESCENDANTS = 200
MAX_MEASUREMENTS = 100


class AssetError(RuntimeError):
    pass


def _declared_levels(tenant):
    """The operator's declared hierarchy, or an empty tuple.

    ``levels`` is a tuple of level names from the root down:
    ``('zavod', 'sex', 'liniya', 'stanok')``. The names are labels for people, not
    a vocabulary the code matches on, so they are kept verbatim and compared only
    for length.
    """
    from .tools import config
    raw = config(tenant).get('assets')
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError('assets must be an object')
    unknown = set(raw) - {'levels', 'entity', 'measurements'}
    if unknown:
        raise ValueError(f'assets has unsupported keys: {sorted(unknown)}')
    levels = raw.get('levels', [])
    if not isinstance(levels, list) or not levels or len(levels) > MAX_LEVELS:
        raise ValueError(f'assets.levels must be a list of 1..{MAX_LEVELS} names')
    seen = set()
    for name in levels:
        if not isinstance(name, str) or not SEGMENT_RE.match(name):
            raise ValueError(f'invalid asset level name: {name!r}')
        if name in seen:
            raise ValueError(f'duplicate asset level name: {name!r}')
        seen.add(name)
    return tuple(levels)


def asset_entity(tenant) -> str:
    """The graph entity backing assets. Defaults to ``asset``."""
    from .tools import config
    raw = config(tenant).get('assets') or {}
    entity = raw.get('entity', 'asset')
    if not isinstance(entity, str) or not business_graph.ENTITY_RE.match(entity):
        raise ValueError('assets.entity must be a valid entity name')
    return entity


def parse_path(tenant, path):
    """Validate a path against the declared hierarchy and return its segments.

    Validation is strict on purpose. A path of the wrong depth is refused rather
    than accepted, because a shorter path and a different machine are the same
    string to a lenient parser, and two assets sharing an identity silently merge
    their history.

    This is the *identity* rule and it is deliberately the only place a path is
    read for identity. Navigation (``children``, ancestor-scoped ``tree``) needs
    to name an intern node, which is a prefix rather than an identity, so it uses
    :func:`prefix_path` and never this function.
    """
    levels = _declared_levels(tenant)
    if not levels:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    segments = _segments(tenant, path, levels)
    if len(segments) != len(levels):
        raise ValueError(f'path must have {len(levels)} levels '
                         f'({" / ".join(levels)}), got {len(segments)}')
    return segments


def prefix_path(tenant, path):
    """Validate a node path anywhere in the hierarchy, root to leaf.

    An intern node has no identity of its own — nothing observes ``zavod-1`` — but
    it is a perfectly good *filter*: "the shops of this plant", "the subtree of
    this line". It is a separate function from :func:`parse_path` so that the
    looser rule can never be reached by accident from an identity call. The depth
    is still bounded by the declared hierarchy, so ``zavod-1/.../stanok-7/extra``
    is refused here too.
    """
    levels = _declared_levels(tenant)
    if not levels:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    segments = _segments(tenant, path, levels)
    if len(segments) > len(levels):
        raise ValueError(f'path must have at most {len(levels)} levels '
                         f'({" / ".join(levels)}), got {len(segments)}')
    return segments


def _segments(tenant, path, levels):
    """The shared shape check: non-empty, bounded, legal segments."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError('path is required')
    text = path.strip().strip('/')
    if len(text) > MAX_PATH_CHARS:
        raise ValueError(f'path must be at most {MAX_PATH_CHARS} characters')
    segments = text.split('/')
    for segment in segments:
        if not SEGMENT_RE.match(segment):
            raise ValueError(f'invalid path segment: {segment!r}')
    if not segments:
        raise ValueError('path is required')
    return segments


def format_path(segments):
    return '/'.join(segments)


def is_descendant(parent_segments, candidate_segments):
    """True when ``candidate`` sits strictly below ``parent``.

    Compared by segments, never by string prefix. ``zavod-1`` is not an ancestor
    of ``zavod-10``: the naive prefix test says it is, and that would let one
    plant's query return another plant's assets.
    """
    if len(candidate_segments) <= len(parent_segments):
        return False
    return candidate_segments[:len(parent_segments)] == list(parent_segments)


def _levels_view(tenant):
    levels = _declared_levels(tenant)
    return {'entity': asset_entity(tenant) if levels else '',
            'levels': list(levels), 'depth': len(levels), 'declared': bool(levels)}


def levels(tenant):
    """The declared hierarchy. A read that tells a manager what shape assets have."""
    return _levels_view(tenant)


def _asset_read(engine, tenant, agent, step):
    """Read every declared asset once, through the graph's own source path.

    Going through ``business_graph`` rather than reading sources directly is what
    keeps this module honest: the graph's preflight runs first, so every source
    authority is proven before any provider I/O, a source may only be a read tool,
    and each value keeps the system that produced it. This module adds no new
    transport and no new authority.

    Returns ``(entry, policy, by_id, status, errors, now)``.
    """
    entity = asset_entity(tenant)
    entry = business_graph.declaration(tenant, entity)
    policy = business_graph.preflight(engine, tenant, agent, entry)
    collected, status, errors, now = business_graph._collect_all(
        engine, tenant, agent, entry, step)
    return entry, policy, collected, status, errors, now


def _placeable(tenant, entity_id):
    """Parse a graph id as a path, or ``None`` when it is not one.

    An id that is not a legal path cannot be placed in the tree. It is skipped
    rather than raising, because one bad row in a customer register must not break
    the whole tree — but it is *never* silently reshaped into a valid path, which
    would invent an asset that does not exist.
    """
    try:
        return parse_path(tenant, entity_id)
    except (ValueError, Forbidden):
        return None


def tree(engine, tenant, agent, step, *, path='', depth=None):
    """The declared hierarchy, optionally bounded to one subtree.

    ``depth`` limits how many levels are returned, which is what keeps a
    five-level plant from returning every station when a manager asked about
    lines. Nodes carry the graph's own status, so a source that could not be read
    is visible rather than looking like an empty tree.
    """
    declared = _declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    if depth is not None:
        if type(depth) is not int or not 1 <= depth <= len(declared):
            raise ValueError(f'depth must be an integer 1..{len(declared)}')
    prefix = prefix_path(tenant, path) if path else []
    entry, policy, collected, status, errors, now = _asset_read(
        engine, tenant, agent, step)
    identity = entry['identity']
    nodes = {}
    for entity_id, observations in collected.items():
        segments = _placeable(tenant, entity_id)
        if segments is None:
            continue
        if prefix and not (segments[:len(prefix)] == prefix):
            continue
        keep = len(prefix) + depth if prefix and depth else (depth or len(declared))
        for cut in range(len(prefix) + 1, min(len(segments), len(prefix) + keep) + 1):
            node_path = format_path(segments[:cut])
            node = nodes.setdefault(node_path, {
                'path': node_path, 'level': declared[cut - 1],
                'depth': cut, 'segments': segments[:cut],
                'is_asset': cut == len(declared)})
            if cut == len(segments):
                node['attributes'] = {
                    name: {'values': value, 'count': len(value)}
                    for name, value in sorted(observations.items())}
    return {
        'entity': asset_entity(tenant), 'identity': identity,
        'levels': list(declared), 'root': format_path(prefix) if prefix else '',
        'nodes': [nodes[key] for key in sorted(nodes)],
        'count': len(nodes),
        # ALWAYS false, and that is the correction. `nodes` is never sliced -- every
        # node the hierarchy produced is in the list above -- so the old
        # `len(nodes) > MAX_CHILDREN` announced a withheld page that did not exist.
        # `tree` takes no `limit` either: it is bounded by `depth`, which the CALLER
        # chooses, so there was no larger value to re-run with. `MAX_CHILDREN` is the
        # children tool's ceiling and is not this tool's. A large tree is visible in
        # `count`, and `depth` or `path` is how a caller asks for less.
        'truncated': False,
        'sources': status, 'source_errors': errors, 'complete': not errors,
        'observed': now, 'authority': {'agent': agent, 'ladder': policy.get('ladder', '')},
    }


def children(engine, tenant, agent, path, step, *, limit=MAX_CHILDREN):
    """Direct children of one node: the next level down, or nothing.

    One level, not a subtree. A manager asking for the children of a plant expects
    its shops, and returning every station underneath would bury the answer.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CHILDREN:
        raise ValueError(f'limit must be an integer 1..{MAX_CHILDREN}')
    declared = _declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    parent = prefix_path(tenant, path)
    if not parent:
        raise ValueError('path is required')
    if len(parent) >= len(declared):
        raise ValueError(f'{format_path(parent)!r} is a leaf level and has no children')
    entry, policy, collected, status, errors, now = _asset_read(
        engine, tenant, agent, step)
    found = {}
    for entity_id in collected:
        segments = _placeable(tenant, entity_id)
        if segments is None:
            continue
        if len(segments) <= len(parent):
            continue
        if segments[:len(parent)] != parent:
            continue
        child_path = format_path(segments[:len(parent) + 1])
        child = found.setdefault(child_path, {
            'path': child_path, 'level': declared[len(parent)],
            'depth': len(parent) + 1, 'segments': segments[:len(parent) + 1],
            'is_asset': len(parent) + 1 == len(declared), 'assets': 0})
        if len(segments) == len(declared):
            child['assets'] += 1
    ordered = [found[key] for key in sorted(found)]
    return {
        'parent': format_path(parent), 'level': declared[len(parent) - 1],
        'child_level': declared[len(parent)],
        'children': ordered[:limit], 'count': len(ordered),
        'truncated': len(ordered) > limit,
        'sources': status, 'source_errors': errors, 'complete': not errors,
        'observed': now, 'authority': {'agent': agent, 'ladder': policy.get('ladder', '')},
    }


def descendants(engine, tenant, agent, path, step, *, limit=MAX_DESCENDANTS):
    """Every asset strictly below one node, bounded.

    This is the call a camera event uses to find what a station belongs to, and
    the call an OEE report uses to roll a line up to its plant. Matching is by
    segments, so ``zavod-1`` never returns ``zavod-10``.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_DESCENDANTS:
        raise ValueError(f'limit must be an integer 1..{MAX_DESCENDANTS}')
    declared = _declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    root = prefix_path(tenant, path)
    entry, policy, collected, status, errors, now = _asset_read(
        engine, tenant, agent, step)
    out, seen = [], 0
    for entity_id in sorted(collected):
        segments = _placeable(tenant, entity_id)
        if segments is None:
            continue
        if not is_descendant(root, segments):
            continue
        seen += 1
        if len(out) >= limit:
            continue
        observations = collected[entity_id]
        out.append({
            'path': entity_id, 'segments': segments,
            'level': declared[len(segments) - 1],
            'is_asset': len(segments) == len(declared),
            'attributes': {name: len(value) for name, value in sorted(observations.items())},
        })
    return {
        'root': format_path(root), 'descendants': out, 'count': seen,
        'returned': len(out), 'truncated': seen > limit,
        'sources': status, 'source_errors': errors, 'complete': not errors,
        'observed': now, 'authority': {'agent': agent, 'ladder': policy.get('ladder', '')},
    }


def resolve(engine, tenant, agent, path, step):
    """One asset with its full graph view: every attribute, with its source.

    The heavy work is the graph's own ``resolve``, so conflicts are reported and
    never silently chosen, and each attribute carries the system that produced it.
    A path that is not in the register is a named refusal, not an empty asset.
    """
    declared = _declared_levels(tenant)
    if not declared:
        raise Forbidden('No asset hierarchy is declared for this tenant')
    segments = parse_path(tenant, path)
    entity = asset_entity(tenant)
    view = business_graph.resolve(engine, tenant, agent, entity, format_path(segments), step)
    view['level'] = declared[len(segments) - 1]
    view['segments'] = segments
    view['is_asset'] = len(segments) == len(declared)
    view['path'] = format_path(segments)
    if not view['attributes'] and not view['source_errors']:
        # Every source answered and none knew this path. That is a real answer:
        # the asset is not in the register.
        view['registered'] = False
    else:
        view['registered'] = bool(view['attributes'])
    return view


def measurements(tenant):
    """Declared measurement names that may later be attached to assets.

    Declared but unused by this block: P11 makes assets exist so that a camera
    event or a cycle time has something to bind to. The names are validated here
    so the operator's vocabulary is checked once, where it is written.
    """
    from .tools import config
    raw = config(tenant).get('assets') or {}
    declared = raw.get('measurements', [])
    if not isinstance(declared, list) or len(declared) > MAX_MEASUREMENTS:
        raise ValueError(f'assets.measurements must be a list of at most {MAX_MEASUREMENTS}')
    clean = []
    for name in declared:
        if not isinstance(name, str) or not SEGMENT_RE.match(name):
            raise ValueError(f'invalid measurement name: {name!r}')
        clean.append(name)
    return clean


# --------------------------------------------------------------- tool handlers


def _levels_tool(engine, tenant, agent, args, step):
    return levels(tenant)


def _tree_tool(engine, tenant, agent, args, step):
    return tree(engine, tenant, agent, step, path=args.get('path', ''),
                depth=args.get('depth'))


def _children_tool(engine, tenant, agent, args, step):
    return children(engine, tenant, agent, args['path'], step,
                    limit=args.get('limit', MAX_CHILDREN))


def _descendants_tool(engine, tenant, agent, args, step):
    return descendants(engine, tenant, agent, args['path'], step,
                       limit=args.get('limit', MAX_DESCENDANTS))


def _resolve_tool(engine, tenant, agent, args, step):
    return resolve(engine, tenant, agent, args['path'], step)


def register_asset_tools(registry):
    """Five read tools. No write path exists here at all.

    Read-only for the same reason the graph is: an entity that can edit the asset
    model would be the author of the identity every event, measurement and report
    depends on. The customer owns their UNS and their register; we read them.
    """
    from .tools import Tool, obj, string
    limit_children = {'type': 'integer', 'minimum': 1, 'maximum': MAX_CHILDREN}
    limit_desc = {'type': 'integer', 'minimum': 1, 'maximum': MAX_DESCENDANTS}
    tools = [
        ('asset.levels', obj({}), _levels_tool),
        ('asset.tree', obj({'path': string(MAX_PATH_CHARS),
                            'depth': {'type': 'integer', 'minimum': 1, 'maximum': MAX_LEVELS}},
                           required=[]), _tree_tool),
        ('asset.children', obj({'path': string(MAX_PATH_CHARS),
                                'limit': limit_children}, ['path']), _children_tool),
        ('asset.descendants', obj({'path': string(MAX_PATH_CHARS),
                                   'limit': limit_desc}, ['path']), _descendants_tool),
        ('asset.resolve', obj({'path': string(MAX_PATH_CHARS)}, ['path']), _resolve_tool),
    ]
    for name, schema, handler in tools:
        if name in registry.items:
            continue
        registry.add(Tool(name, 'read', schema, handler))
