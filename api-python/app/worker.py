"""Run separately: python -m app.worker. No provider calls during webhook acceptance."""
import logging
import os
import signal
import time
from .platform_api import engine,agents
from .packs import PACKS_DIR
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.agent_planner import ResultPlanner
from platform_runtime.briefing import Briefing
from platform_runtime.reengagement import ReengagementLoop
from platform_runtime.escalation import EscalationLoop

stop=False

def shutdown(*_):
    global stop
    stop=True


def main():
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    from .planning import planner as make_planner
    e=engine();planner=make_planner(e)
    agent_loop=AgentLoop(e);result_planner=ResultPlanner(e)
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
            try:
                e.run_schedules(tenant)
                active=reengagement.tick(tenant) or active
                active=briefing.tick(tenant) or active
                active=escalation.tick(tenant) or active
                active=e.process_event(tenant,planner) or active
                active=e.tick(tenant,'cloud:'+str(os.getpid())) or active
                active=agent_loop.tick(tenant,result_planner) or active
            except Exception as exc:
                logging.error('worker tenant=%s exception_type=%s',tenant,type(exc).__name__)
        time.sleep(0.1 if active else 1)

if __name__=='__main__':main()
