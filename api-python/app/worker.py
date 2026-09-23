"""Run separately: python -m app.worker. No provider calls during webhook acceptance.

Stage isolation: every coordinator for a tenant runs inside its own guard.  With
one shared try/except a single coordinator that raised something it does not
catch internally (reengagement.tick, for example, handles only
Forbidden/Conflict/ValueError/LookupError, so a RuntimeError from a missing
integrations entry escaped) skipped the tenant's remaining stages -- the event
pump, the engine tick and the agent loop -- and, having never advanced its own
schedule, was due again a second later.  One misconfigured pack key starved the
tenant's whole engine.  A failing stage must cost that stage only.

Only the exception TYPE is logged.  An exception message may carry a provider
URL or a bot token, and the worker log is not a secret store.

The FastAPI layer, the pack loader and the planner are imported inside main() so
run_stages stays importable without FastAPI or a validated ENV -- the same
reason app/planning.py defers its agent listing import.
"""
import logging
import os
import signal
import time
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.agent_planner import ResultPlanner
from platform_runtime.briefing import Briefing
from platform_runtime.conversation import ConversationTurns
from platform_runtime.reengagement import ReengagementLoop
from platform_runtime.escalation import EscalationLoop

stop=False

def shutdown(*_):
    global stop
    stop=True


def run_stages(tenant,stages,log=logging.error):
    """Run each (name, callable) stage for one tenant; one failing stage never skips the next.

    Returns True if any stage reported work."""
    active=False
    for name,stage in stages:
        try:
            active=bool(stage()) or active
        except Exception as exc:
            # Type only: str(exc) may contain a provider URL or a credential.
            log('worker tenant=%s stage=%s exception_type=%s',tenant,name,type(exc).__name__)
    return active


def main():
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    from .platform_api import engine
    from .packs import PACKS_DIR
    from .planning import planner as make_planner
    e=engine();planner=make_planner(e)
    agent_loop=AgentLoop(e);result_planner=ResultPlanner(e)
    conversation=ConversationTurns(e,agent_loop)
    reengagement=ReengagementLoop(e,agent_loop)
    briefing=Briefing(e)
    escalation=EscalationLoop(e)
    # No supervisor tick here on purpose. Routing is an on-demand control-plane
    # action (POST /{tenant}/supervisor/route), not a schedule: a manager asks a
    # question when they have one. A tick would have nothing to look for, and the
    # hop cap is enforced inside Supervisor.route rather than by a loop.
    while not stop:
        active=False
        for folder in PACKS_DIR.iterdir():
            if not folder.is_dir() or folder.name.startswith('_'):continue
            tenant=folder.name
            active=run_stages(tenant,[
                ('schedules',lambda t=tenant:e.run_schedules(t)),
                ('reengagement',lambda t=tenant:reengagement.tick(t)),
                ('briefing',lambda t=tenant:briefing.tick(t)),
                ('escalation',lambda t=tenant:escalation.tick(t)),
                ('process_event',lambda t=tenant:e.process_event(t,planner)),
                ('engine',lambda t=tenant:e.tick(t,'cloud:'+str(os.getpid()))),
                ('agent_loop',lambda t=tenant:agent_loop.tick(t,result_planner)),
                # After the loop so a run that just ended is settled in the same pass.
                ('conversation',lambda t=tenant:conversation.tick(t)),
            ]) or active
        time.sleep(0.1 if active else 1)

if __name__=='__main__':main()
