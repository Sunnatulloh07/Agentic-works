"""Recheck directory state inside the engine's own SQL transaction."""
from platform_runtime.engine import OPERATOR_CHANNEL, Forbidden
from .authorization import directory_enabled

# Channels whose task creator or event sender is a workspace member, re-checked
# inside the engine's transaction. Provider streams (telegram, instagram, ...) carry
# external customers and are not listed. 'operator' is the reserved channel of
# dashboard replies to a customer (Engine.submit_operator_reply).
MEMBER_CHANNELS = frozenset({'web', 'cron', 'approval', 'agent', OPERATOR_CHANNEL})


def runtime_authority(c, tenant, channel='', actor='', roles=('owner','operator')):
    if not directory_enabled():return
    workspace=c.execute('SELECT status FROM p_workspaces WHERE id=?',(tenant,)).fetchone()
    if not workspace or workspace['status']!='active':raise Forbidden('Workspace unavailable')
    if channel in MEMBER_CHANNELS:
        row=c.execute('''SELECT m.role FROM p_memberships m JOIN p_users u ON u.id=m.user_id
          WHERE m.workspace_id=? AND m.user_id=? AND m.status='active' AND u.status='active' ''',(tenant,actor)).fetchone()
        if not row or row['role'] not in roles:raise Forbidden('Actor permission revoked')
