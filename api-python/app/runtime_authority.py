"""Recheck directory state inside the engine's own SQL transaction."""
from platform_runtime.engine import Forbidden
from .authorization import directory_enabled


def runtime_authority(c, tenant, channel='', actor='', roles=('owner','operator')):
    if not directory_enabled():return
    workspace=c.execute('SELECT status FROM p_workspaces WHERE id=?',(tenant,)).fetchone()
    if not workspace or workspace['status']!='active':raise Forbidden('Workspace unavailable')
    if channel in {'web','cron','approval','agent'}:
        row=c.execute('''SELECT m.role FROM p_memberships m JOIN p_users u ON u.id=m.user_id
          WHERE m.workspace_id=? AND m.user_id=? AND m.status='active' AND u.status='active' ''',(tenant,actor)).fetchone()
        if not row or row['role'] not in roles:raise Forbidden('Actor permission revoked')
