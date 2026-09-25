"""Operator takeover reply, after delivery: the human's line joins the conversation.

``Engine.operator_reply`` creates the send, and every authorisation decision lives
there and in the claim path. This module only settles what that path produced:
once an operator reply task has SUCCEEDED, its text is appended to
``p_conversation_history`` with role ``operator``, exactly once.

"Once" is keyed by the task. A ``p_records`` marker (kind OPERATOR_LINE_KIND,
id = task id, body naming the actor) is inserted with INSERT OR IGNORE in the
same transaction as the history line, so a second pass, a second worker or a
crash between the two can neither add the line twice nor lose it. A failed,
cancelled or uncertain reply never joins the history: the customer either did
not get it or nobody knows, and the conversation's next turn must not read it as
said. A reply an owner reconciles to ``succeeded`` does join it -- the owner has
confirmed the customer has it.

The history helpers are conversation.py's own, so the ceiling on history rows is
applied to an operator line exactly as to a customer or agent line.
"""
import json

from .conversation import _append, _next_seq
from .engine import DIRECT_DESTINATION_FIELD, OPERATOR_CHANNEL, encode

OPERATOR_LINE_KIND = 'conversation.operator_line'
OPERATOR_ROLE = 'operator'
# Delivered operator replies settled per call. A human types them, so a backlog
# larger than this means the worker was down; the next pass takes the rest.
MAX_SETTLE_BATCH = 50

_PENDING = '''SELECT t.id,t.actor,s.tool,s.args FROM p_tasks t
  JOIN p_steps s ON s.tenant=t.tenant AND s.task=t.id AND s.position=0
  WHERE t.tenant=? AND t.channel=? AND t.status='succeeded'
  AND NOT EXISTS(SELECT 1 FROM p_records r WHERE r.tenant=t.tenant AND r.kind=? AND r.id=t.id)
  ORDER BY t.updated,t.created LIMIT ?'''


def settle_operator_replies(engine, tenant):
    """Append each delivered operator reply of ``tenant`` to its conversation once.

    Returns True when at least one line was added. Reads first, so an idle pass
    takes no write lock; the marker insert decides who settles a task.
    """
    args = (tenant, OPERATOR_CHANNEL, OPERATOR_LINE_KIND, MAX_SETTLE_BATCH)
    with engine.read() as c:
        if not c.execute(_PENDING, args).fetchone():
            return False
    added = 0
    with engine.tx() as c:
        for row in c.execute(_PENDING, args).fetchall():
            channel = row['tool'][:-len('.send')]
            step = json.loads(row['args'])
            conversation_id = step[DIRECT_DESTINATION_FIELD[row['tool']]]
            now = engine.clock()
            marked = c.execute('INSERT OR IGNORE INTO p_records VALUES(?,?,?,?,?)',
                               (tenant, OPERATOR_LINE_KIND, row['id'],
                                encode({'actor': row['actor'], 'channel': channel,
                                        'conversation_id': conversation_id}), now)).rowcount
            if not marked:
                continue
            _append(c, tenant, channel, conversation_id,
                    _next_seq(c, tenant, channel, conversation_id), OPERATOR_ROLE, step['text'], now)
            engine.audit(c, tenant, row['id'], 'conversation.operator_delivered', row['actor'],
                         {'channel': channel})
            added += 1
    return added > 0
