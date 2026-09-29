"""Run separately: python -m app.worker. No provider calls during webhook acceptance.

Stage isolation: every coordinator for a tenant runs inside its own guard.  With
one shared try/except a single coordinator that raised something it does not
catch internally (reengagement.tick, for example, handles only
Forbidden/Conflict/ValueError/LookupError, so a RuntimeError from a missing
integrations entry escaped) skipped the tenant's remaining stages -- the event
pump, the engine tick and the agent loop -- and, having never advanced its own
schedule, was due again a second later.  One misconfigured pack key starved the
tenant's whole engine.  A failing stage must cost that stage only.

Throughput: the loop used to do, per tenant per pass, at most one engine step,
one conversation transition and ONE model call, then sleep 0.1 s -- twenty
customers writing at once were answered one model call at a time.  Now the
cheap stages drain (``drain``: repeated until idle, bounded per pass) and the
agent loop's model calls run on a small thread pool (``PlannerPool``: at most
``PLANNER_CONCURRENCY_PER_TENANT`` per tenant, tenants taken round-robin).  A
model call already reserves, claims and leases its run in SQL before it starts
and commits only under that claim (AgentLoop._reserve / _commit), so two calls
can never plan one run; no transaction is open while a call waits.

Inbound events split the same way (``EventRouting``).  A channel a conversation
agent owns plans without a model call -- the planner returns the turn -- so its
events stay in the drain and are answered in the same pass.  Every other channel
(the dashboard's ``web``, for one) can reach the one-shot LLM planner, which may
wait up to llm.EVENT_PLAN_DEADLINE_SECONDS; the pool claims those events
(Engine.claim_event: one short write and a lease) and plans them on its threads
(Engine.settle_event).  One slow plan used to hold every tenant's drain.

Only the exception TYPE is logged.  An exception message may carry a provider
URL or a bot token, and the worker log is not a secret store.

The FastAPI layer, the pack loader and the planner are imported inside main() so
run_stages stays importable without FastAPI or a validated ENV -- the same
reason app/planning.py defers its agent listing import.
"""
import concurrent.futures
import functools
import logging
import os
import signal
import time
from platform_runtime.agent_loop import AgentLoop
from platform_runtime.agent_planner import ResultPlanner
from platform_runtime.briefing import Briefing
from platform_runtime.conversation import ConversationTurns
from platform_runtime.engine import NotFound
from platform_runtime.operator_reply import settle_operator_replies
from platform_runtime.reengagement import ReengagementLoop
from platform_runtime.escalation import EscalationLoop
from platform_runtime.usage_budget import UsageBudget

# Per pass, per tenant: repeat the cheap stages at most this many rounds, and stop
# early once this much wall time is spent, so one busy tenant cannot starve the rest.
MAX_DRAIN_ROUNDS = 50
DRAIN_SECONDS = 2.0
# Model calls in flight at once: per tenant, and in total for this process. The
# per-tenant bound matches the default usage-budget max_inflight (4); a tenant
# whose budget allows fewer parallel calls is held to its budget.
PLANNER_CONCURRENCY_PER_TENANT = 4
PLANNER_WORKERS = 8

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


def drain(tenant,stages,log=logging.error,rounds=MAX_DRAIN_ROUNDS,seconds=DRAIN_SECONDS,clock=time.monotonic):
    """Repeat cheap stages until none reports work, at most `rounds` times or `seconds`.

    Each round is one run_stages call, so isolation is unchanged. Returns True if
    any round reported work."""
    active=False
    started=clock()
    for _ in range(rounds):
        if not run_stages(tenant,stages,log):break
        active=True
        if clock()-started>=seconds:break
    return active


def conversation_channels(listing,tenant,owner):
    """Channels whose inbound events plan without a model: a conversation agent owns them.

    ``owner`` is app.planning.conversation_agent, passed in so this module's
    top-level imports stay free of the API layer. Asking it channel by channel
    keeps one statement of the rule; this only lists the candidates, the sources
    of the pack's message triggers."""
    sources={trigger.get('source') for agent in listing(tenant) for trigger in agent.get('triggers') or []}
    return frozenset(s for s in sources if isinstance(s,str) and owner(listing,tenant,s))


class EventRouting:
    """Which thread plans an inbound event: the main loop or the planner pool.

    ``fast(tenant)`` names the channels whose events plan without a model call.
    Their events are claimed and settled on the main thread (``process_fast``);
    every other event is claimed by the pool (``claim``) and planned on one of
    its threads (``plan``). The claims are disjoint by channel, and each is a
    lease, so no event is held by both. If ``fast`` itself fails (a pack that
    will not load), every event goes to the pool, whose planner meets the same
    failure and records it on the event -- rather than leaving it pending.
    """

    def __init__(self,engine,planner,fast):
        self.engine,self.planner,self.fast=engine,planner,fast

    def _fast(self,tenant):
        try:
            return frozenset(self.fast(tenant))
        except Exception:
            return frozenset()

    def process_fast(self,tenant):
        return self.engine.process_event(tenant,self.planner,channels=self._fast(tenant))

    def claim(self,tenant):
        return self.engine.claim_event(tenant,skip=self._fast(tenant))

    def plan(self,tenant,event):
        return self.engine.settle_event(tenant,event,self.planner)


class PlannerPool:
    """Model calls on a thread pool, bounded per tenant, round-robin.

    Two kinds of call share it, and so share the per-tenant bound and the usage
    budget's parallel limit: an agent-loop run (``AgentLoop.plan``) and, when
    ``events`` is given, the plan of an inbound event on a channel that may need
    a model (``EventRouting.plan``). ``pump`` runs on the worker's main thread:
    it collects finished calls, then takes work with one short SQL write each --
    a run reservation or an event claim, both leased -- and submits the call.
    It never submits more than ``workers`` calls, so a submitted call always
    starts at once and nothing it holds waits in a queue while its lease runs
    down.
    """

    def __init__(self,loop,planner,*,events=None,workers=PLANNER_WORKERS,
                 per_tenant=PLANNER_CONCURRENCY_PER_TENANT,log=logging.error):
        self.loop,self.planner,self.events=loop,planner,events
        self.workers,self.per_tenant,self.log=workers,per_tenant,log
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='planner')
        self.inflight={}  # future -> (tenant, run_id); run_id None for an inbound event
        self.turn=0

    def _reap(self):
        done=[future for future in self.inflight if future.done()]
        for future in done:
            tenant,run=self.inflight.pop(future)
            error=future.exception()
            if error is not None:
                stage='process_event' if run is None else 'agent_loop'
                self.log('worker tenant=%s stage=%s exception_type=%s',tenant,stage,type(error).__name__)
        return bool(done)

    def _held(self,tenant):
        return {run for owner,run in self.inflight.values() if owner==tenant and run is not None}

    def _run(self,tenant):
        """(changed, (run_id, call) or None): reserve the tenant's next agent-loop run."""
        changed,run_id,reservation=self.loop.reserve_next(tenant,self._held(tenant))
        if reservation is None:return changed,None
        return changed,(run_id,functools.partial(self.loop.plan,tenant,run_id,reservation,self.planner))

    def _event(self,tenant):
        """(changed, (None, call) or None): claim the tenant's next model-channel event."""
        event=self.events.claim(tenant)
        if event is None:return False,None
        return True,(None,functools.partial(self.events.plan,tenant,event))

    def _room(self,tenant):
        """How many more calls this tenant may start now."""
        running=sum(1 for owner,_ in self.inflight.values() if owner==tenant)
        room=self.per_tenant-running
        try:
            budget=UsageBudget(self.loop.engine).summary(tenant)
        except NotFound:
            return room
        # A call beyond the budget's parallel limit would be refused after its
        # run was reserved -- and the refusal would escalate the run.
        return min(room,budget['max_inflight']-running,budget['max_inflight']-budget['inflight'])

    def pump(self,tenants):
        """Collect finished calls, then start new ones round-robin. True if anything moved.

        Each (tenant, source) is its own guard, as run_stages is: a tenant whose
        run reservation raises still has its events claimed, and the reverse."""
        progressed=self._reap()
        tenants=list(tenants)
        if not tenants:return progressed
        start=self.turn%len(tenants);self.turn+=1
        sources=[('agent_loop',self._run)]+([('process_event',self._event)] if self.events else [])
        order=[(tenant,stage,take) for tenant in tenants[start:]+tenants[:start] for stage,take in sources]
        room={}
        while order and len(self.inflight)<self.workers:
            for entry in list(order):
                if len(self.inflight)>=self.workers:break
                tenant,stage,take=entry
                try:
                    if tenant not in room:room[tenant]=self._room(tenant)
                    if room[tenant]<=0:order.remove(entry);continue
                    changed,job=take(tenant)
                except Exception as exc:
                    self.log('worker tenant=%s stage=%s exception_type=%s',tenant,stage,type(exc).__name__)
                    order.remove(entry);continue
                progressed=progressed or changed
                if job is None:order.remove(entry);continue
                run_id,call=job
                room[tenant]-=1
                self.inflight[self.executor.submit(call)]=(tenant,run_id)
        return progressed

    def idle(self,timeout):
        """Sleep up to `timeout`, waking as soon as a model call finishes."""
        if self.inflight:
            concurrent.futures.wait(list(self.inflight),timeout=timeout,
                                    return_when=concurrent.futures.FIRST_COMPLETED)
        else:
            time.sleep(timeout)

    def shutdown(self):
        # Every submitted call is already running (see the class docstring), so
        # waiting lets each one commit under its lease instead of stranding it.
        # A process killed before that is recovered by lease expiry instead.
        self.executor.shutdown(wait=True)


def main():
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    from .platform_api import agents as listing, engine
    from .packs import PACKS_DIR
    from .planning import conversation_agent, planner as make_planner
    e=engine();planner=make_planner(e,listing)
    agent_loop=AgentLoop(e);result_planner=ResultPlanner(e)
    conversation=ConversationTurns(e,agent_loop)
    reengagement=ReengagementLoop(e,agent_loop)
    briefing=Briefing(e)
    escalation=EscalationLoop(e)
    events=EventRouting(e,planner,lambda t:conversation_channels(listing,t,conversation_agent))
    pool=PlannerPool(agent_loop,result_planner,events=events)
    # No supervisor tick here on purpose. Routing is an on-demand control-plane
    # action (POST /{tenant}/supervisor/route), not a schedule: a manager asks a
    # question when they have one. A tick would have nothing to look for, and the
    # hop cap is enforced inside Supervisor.route rather than by a loop.
    try:
        while not stop:
            tenants=[folder.name for folder in sorted(PACKS_DIR.iterdir())
                     if folder.is_dir() and not folder.name.startswith('_')]
            # Model calls first: collect the ones that finished, start new ones
            # (agent-loop runs, and events on channels that may need a model).
            active=run_stages('*',[('agent_loop',lambda:pool.pump(tenants))])
            for tenant in tenants:
                active=run_stages(tenant,[
                    ('schedules',lambda t=tenant:e.run_schedules(t)),
                    ('reengagement',lambda t=tenant:reengagement.tick(t)),
                    ('briefing',lambda t=tenant:briefing.tick(t)),
                    ('escalation',lambda t=tenant:escalation.tick(t)),
                ]) or active
                active=drain(tenant,[
                    # Conversation channels only: no model call on this thread.
                    ('process_event',lambda t=tenant:events.process_fast(t)),
                    ('engine',lambda t=tenant:e.tick(t,'cloud:'+str(os.getpid()))),
                    ('operator_reply',lambda t=tenant:settle_operator_replies(e,t)),
                    # After the pump, so a run that just ended is settled in the same pass.
                    ('conversation',lambda t=tenant:conversation.tick(t)),
                ]) or active
            pool.idle(0.1 if active else 1)
    finally:
        pool.shutdown()

if __name__=='__main__':main()
