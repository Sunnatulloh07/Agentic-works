"""Versioned control plane for channel-independent execution."""
import os
import time
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ConfigDict
from .identity_store import AuthenticationError
from platform_runtime.engine import Engine, Conflict, Forbidden, NotFound, RateLimited
from platform_runtime.tools import build_registry
from platform_runtime.llm import Planner
from .packs import load_pack
from .auth import verify_claims, issue_token

router=APIRouter(prefix='/platform',tags=['platform'])

# ---------------------------------------------------------------------------
# Declared bounds (§153).
#
# Every limit the control plane enforces is named here, and every name is
# asserted literally in ``runtime_tests/test_control_plane_bounds.py``.  Inline
# ``Field(ge=..., le=...)`` values are not addressable from a test, so widening
# one used to be silent: the suite stayed green while the control plane began
# accepting wider autonomous behaviour.
# ---------------------------------------------------------------------------

MIN_NON_EMPTY = 1

# Text ceilings.
MAX_IDENTIFIER_CHARS = 128
MAX_KEY_CHARS = 256
MAX_EXTERNAL_ID_CHARS = 256
MAX_EVIDENCE_CHARS = 500
MAX_TEXT_CHARS = 4000
MAX_URL_CHARS = 2000
MAX_TITLE_CHARS = 200
MAX_MODEL_CHARS = 256
MAX_CONTENT_CHARS = 100_000
MAX_QUERY_CHARS = 500
MAX_QUESTION_CHARS = 2000
MAX_DISPLAY_NAME_CHARS = 256
MAX_CONTACT_VALUE_CHARS = 512
MAX_CHANNEL_CHARS = 32
MAX_BRIEFING_TITLE_CHARS = 120
MAX_SECTION_REF_CHARS = 64
MAX_KEYWORDS_PER_SECTION = 20

# Vector and knowledge ceilings.
MAX_VECTOR_DIMENSION = 1024
MAX_VECTORS_PER_DOCUMENT = 224
MIN_KNOWLEDGE_RESULTS = 1
MAX_KNOWLEDGE_RESULTS = 5
DEFAULT_KNOWLEDGE_RESULTS = 4
MIN_DIMENSION = 0
MAX_DIMENSION = 1024
MIN_VERSION = 0
MIN_DELETE_VERSION = 1
MAX_VERSION = 2 ** 31  # exclusive: the fields use ``lt=``

# Money, in minor units.
MIN_MONEY_MINOR = 0
MAX_MONEY_MINOR = 10 ** 15
MIN_LIMIT_MICRO = 1
MAX_LIMIT_MICRO = 10 ** 15
MIN_ACTUAL_MICRO = 0
MIN_MAX_INFLIGHT = 1
MAX_MAX_INFLIGHT = 100
DEFAULT_MAX_INFLIGHT = 4

# Step ceilings.
MIN_SUBMIT_STEPS = 1
MAX_SUBMIT_STEPS = 20
MIN_SECTIONS = 1
MAX_SECTIONS = 12
DEFAULT_MAX_SECTIONS = 6
MIN_AGENT_RUN_STEPS = 1
MAX_AGENT_RUN_STEPS = 12
DEFAULT_AGENT_RUN_STEPS = 6
DEFAULT_REENGAGEMENT_STEPS = 4
DEFAULT_SUPERVISOR_STEPS = 6
MIN_SUPERVISOR_HOPS = 1
MAX_SUPERVISOR_HOPS = 3

# Wall-clock ceilings, in seconds.
MIN_MAX_SECONDS = 60
MAX_MAX_SECONDS = 86_400
DEFAULT_MAX_SECONDS = 1800
MIN_SCHEDULE_INTERVAL_SECONDS = 60
MAX_SCHEDULE_INTERVAL_SECONDS = 31_536_000

# Re-engagement autonomy ceilings.  These govern how often the platform may act
# on a customer without a human in the loop, so they are the ones worth pinning.
MIN_INACTIVE_MINUTES = 1
MAX_INACTIVE_MINUTES = 20_160
DEFAULT_INACTIVE_MINUTES = 120
MIN_COOLDOWN_SECONDS = 300
MAX_COOLDOWN_SECONDS = 2_592_000
DEFAULT_COOLDOWN_SECONDS = 86_400
MIN_REENGAGEMENT_ATTEMPTS = 1
MAX_REENGAGEMENT_ATTEMPTS = 10
DEFAULT_REENGAGEMENT_ATTEMPTS = 2
MIN_REENGAGEMENT_PER_CYCLE = 1
MAX_REENGAGEMENT_PER_CYCLE = 20
DEFAULT_REENGAGEMENT_PER_CYCLE = 5
MIN_CYCLE_INTERVAL_SECONDS = 300
MAX_CYCLE_INTERVAL_SECONDS = 604_800
DEFAULT_CYCLE_INTERVAL_SECONDS = 3600

# Escalation autonomy ceilings.
MIN_ESCALATION_PER_CYCLE = 1
MAX_ESCALATION_PER_CYCLE = 50
DEFAULT_ESCALATION_PER_CYCLE = 10
MIN_MAX_AGE_DAYS = 1
MAX_MAX_AGE_DAYS = 365
DEFAULT_MAX_AGE_DAYS = 30

# Briefing ceilings.
MIN_HOUR = 0
MAX_HOUR = 23
DEFAULT_HOUR = 8
MIN_MINUTE = 0
MAX_MINUTE = 59
DEFAULT_MINUTE = 0
MIN_TIMEZONE_OFFSET_MINUTES = -1440
MAX_TIMEZONE_OFFSET_MINUTES = 1440
DEFAULT_TIMEZONE_OFFSET_MINUTES = 300
MIN_BRIEFING_ROWS = 1
MAX_BRIEFING_ROWS = 50
DEFAULT_BRIEFING_ROWS = 5



def agents(tenant):
    # triggers and conversation let app/planning.py pick a conversation agent by
    # capability (a message trigger on the inbound channel), never by agent id.
    return [{'id':a.id,'name':a.name,'department':a.department,'tools':a.tools,'ladder':a.ladder,
             'triggers':[t.model_dump() for t in a.triggers],'conversation':a.conversation.model_dump()}
            for a in load_pack(tenant).agents]


def policy(tenant,agent):
    a=next((x for x in load_pack(tenant).agents if x.id==agent),None)
    if a is None:raise Forbidden('Agent not in tenant pack')
    # persona and conversation shape what an agent proposes, not what it may do:
    # both are DESCRIPTIVE_POLICY_KEYS, so editing them keeps granted approvals.
    return {'tools':a.tools,'ladder':a.ladder,'approval':a.approval.required_for,'approver_role':a.approval.approver_role,'independent_approval':a.approval.independent,'allowed_recipients':a.allowed_recipients,'allowed_connections':a.allowed_connections,'persona':a.prompt,
            'conversation':a.conversation.model_dump()}


def catalog(tenant,query):
    # product_view, not model_dump: the model gets stock and in-stock sizes and a
    # bounded description, so a search hit fits one observation.
    from .tools import product_view,products_search
    return [product_view(p) for p in products_search(load_pack(tenant),query)]


def shop_data(tenant):
    """The tenant's shop as plain data for shop.info / orders.draft (platform_runtime.shop_tools).

    The runtime reads it through this injected reader and never imports a pack.
    """
    pack=load_pack(tenant)
    return {'shop_name':pack.shop_name,'faq':dict(pack.faq),
            'branches':[b.model_dump() for b in pack.branches],
            'products':[{'id':p.id,'name':p.name,'price_uzs':p.price_uzs,'sizes':list(p.sizes),
                         'stock':dict(p.stock)} for p in pack.products]}


def engine():
    from .storage import _path, db
    from .runtime_authority import runtime_authority
    db()  # Ensure directory migrations before the engine opens its independent connections.
    return Engine(_path(),build_registry(catalog,shop_data),policy,authority=runtime_authority)


def identity(request,tenant,roles=('owner','operator','integrator','viewer')):
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer '):raise HTTPException(401,'Bearer token required')
    try:claims=verify_claims(auth[7:])
    except Exception:raise HTTPException(401,'Invalid token')
    if claims.get('tenant_id')!=tenant or claims.get('token_type')!='user':raise HTTPException(403,'Tenant or token type mismatch')
    if claims.get('role') not in roles:raise HTTPException(403,'Role not permitted')
    return claims


def call(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except RateLimited:raise HTTPException(429,'Tenant quota exhausted')
    except (Forbidden, AuthenticationError):raise HTTPException(403,'Policy denied')
    except NotFound:raise HTTPException(404,'Not found')
    except Conflict as e:raise HTTPException(409,str(e))
    except (ValueError,LookupError):raise HTTPException(422,'Invalid input or unavailable tool')


class StrictRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Submit(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    key:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_KEY_CHARS)
    steps:list[dict]=Field(min_length=MIN_SUBMIT_STEPS,max_length=MAX_SUBMIT_STEPS)


@router.get('/{tenant}/identity')
def get_identity(tenant:str,request:Request):
    who=identity(request,tenant)
    with engine().read() as c:
        frozen=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
    return {'tenant_id':tenant,'subject':who['sub'],'role':who['role'],
            'frozen':bool(frozen and frozen['stopped'])}


@router.get('/{tenant}/catalog')
def get_catalog(tenant:str,request:Request):
    identity(request,tenant)
    e=engine()
    pack_agents=agents(tenant)
    assigned={name for agent in pack_agents for name in agent['tools']}
    return {'agents':pack_agents,'tools':e.registry.describe(assigned),'version':'0.3.8-development-preview'}


@router.post('/{tenant}/tasks')
def submit(tenant:str,req:Submit,request:Request):
    who=identity(request,tenant,('owner','operator'))
    tid=call(engine().submit,tenant,'web',req.key,req.agent,req.steps,who['sub'])
    return {'task_id':tid}


@router.get('/{tenant}/tasks')
def tasks(tenant:str,request:Request):
    identity(request,tenant)
    return {'tasks':engine().list_tasks(tenant)}


@router.get('/{tenant}/tasks/{task}')
def task_get(tenant:str,task:str,request:Request):
    identity(request,tenant)
    return call(engine().get,tenant,task)


class Decision(StrictRequest):
    decision:Literal['approved','rejected']


@router.post('/{tenant}/steps/{step}/approval')
def decide(tenant:str,step:str,req:Decision,request:Request):
    who=identity(request,tenant,('owner','operator'))
    call(engine().approve,tenant,step,who['sub'],req.decision,who['role'])
    return {'ok':True}


@router.post('/{tenant}/tasks/{task}/cancel')
def cancel(tenant:str,task:str,request:Request):
    who=identity(request,tenant,('owner','operator'))
    call(engine().cancel,tenant,task,who['sub']);return {'ok':True}


class Reconcile(StrictRequest):
    outcome:Literal['succeeded','failed']
    evidence:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_EVIDENCE_CHARS)


@router.post('/{tenant}/steps/{step}/reconcile')
def reconcile(tenant:str,step:str,req:Reconcile,request:Request):
    who=identity(request,tenant,('owner',))
    call(engine().reconcile,tenant,step,who['sub'],who['role'],req.outcome,req.evidence)
    return {'ok':True}


class Freeze(StrictRequest):stopped:bool


@router.post('/{tenant}/freeze')
def freeze(tenant:str,req:Freeze,request:Request):
    who=identity(request,tenant,('owner',))
    call(engine().freeze,tenant,req.stopped,who['sub']);return {'ok':True,'stopped':req.stopped}


@router.get('/{tenant}/audit')
def audit(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    with engine().read() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM p_audit WHERE tenant=? ORDER BY id DESC LIMIT 200',(tenant,))]
    return {'events':rows}


@router.get('/{tenant}/inbox')
def inbox(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    with engine().read() as c:
        rows=[dict(r) for r in c.execute('SELECT channel,event_key,status,result,error FROM p_events WHERE tenant=? ORDER BY rowid DESC LIMIT 100',(tenant,))]
    return {'events':rows}


class RetryEvent(StrictRequest):
    channel:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_CHANNEL_CHARS)
    key:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_KEY_CHARS)


@router.post('/{tenant}/inbox/retry')
def retry_event(tenant:str,req:RetryEvent,request:Request):
    who=identity(request,tenant,('owner','operator'))
    call(engine().retry_event,tenant,req.channel,req.key,who['sub']);return {'ok':True}


class DeviceRequest(StrictRequest):
    device_id:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS,pattern=r'^[A-Za-z0-9_-]+$')
    revoked:bool=False


@router.post('/{tenant}/devices')
def device(tenant:str,req:DeviceRequest,request:Request):
    who=identity(request,tenant,('owner',))
    e=engine()
    generation=call(e.device,tenant,req.device_id,req.revoked,actor=who['sub'])
    if req.revoked:return {'ok':True}
    return {'device_token':issue_token(tenant,subject=req.device_id,role='device',token_type='device',device_id=req.device_id,generation=generation)}


@router.get('/{tenant}/devices')
def devices(tenant:str,request:Request):
    identity(request,tenant)
    with engine().read() as c:rows=[dict(r) for r in c.execute('SELECT * FROM p_devices WHERE tenant=?',(tenant,))]
    return {'devices':rows}


class ScheduleRequest(Submit):
    interval_seconds:int=Field(ge=MIN_SCHEDULE_INTERVAL_SECONDS,le=MAX_SCHEDULE_INTERVAL_SECONDS)


@router.post('/{tenant}/schedules')
def schedule(tenant:str,req:ScheduleRequest,request:Request):
    who=identity(request,tenant,('owner',))
    call(engine().schedule,tenant,req.key,req.agent,req.steps,req.interval_seconds,actor=who['sub'])
    return {'ok':True}


class EventRequest(StrictRequest):
    key:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_KEY_CHARS)
    text:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_TEXT_CHARS)


@router.post('/{tenant}/events')
def event(tenant:str,req:EventRequest,request:Request):
    who=identity(request,tenant,('owner','operator'))
    return call(engine().accept_event,tenant,'web',req.key,{'text':req.text,'sender':who['sub'],'conversation_id':who['sub']})


@router.websocket('/runner/ws')
async def runner(ws:WebSocket):
    import asyncio
    await ws.accept()
    try:
        hello=await asyncio.wait_for(ws.receive_json(),10)
        token=str(hello.get('token',''))
        claims=verify_claims(token)
        if claims.get('token_type')!='device':raise ValueError('Device token required')
        tenant,device=claims['tenant_id'],claims['device_id']
        e=engine()
        while True:
            # Token expiry and device rotation checked for EVERY message, not only hello.
            msg=await asyncio.wait_for(ws.receive_json(),65)
            claims=verify_claims(token)
            with e.tx() as c:
                d=c.execute('SELECT * FROM p_devices WHERE tenant=? AND id=?',(tenant,device)).fetchone()
                if not d or d['revoked'] or d['generation']!=claims['generation']:raise Forbidden('Device revoked')
                c.execute('UPDATE p_devices SET seen=? WHERE tenant=? AND id=?',(time.time(),tenant,device))
                stop=c.execute('SELECT stopped FROM p_freeze WHERE tenant=?',(tenant,)).fetchone()
            if msg.get('type')=='heartbeat':
                step=e.claim(tenant,'device:'+device,device=device,lease_seconds=90)
                tasks=[] if not step else [{'id':step['id'],'tool':step['tool'],'params':step['args'],'claim':step['claim'],'lease':step['lease']}]
                await ws.send_json({'type':'heartbeat_ack','stopped':bool(stop and stop['stopped']),'tasks':tasks})
            elif msg.get('type')=='result':
                sid=str(msg.get('id',''));claim=str(msg.get('claim',''))
                with e.read() as c:
                    owned=c.execute('SELECT 1 FROM p_steps WHERE tenant=? AND id=? AND device=? AND claim=?',(tenant,sid,device,claim)).fetchone()
                if not owned:raise Forbidden('Unowned step')
                outcome='uncertain' if msg.get('uncertain') else ('succeeded' if msg.get('ok') is True else 'failed')
                e.finish(tenant,sid,claim,msg.get('result',{}),outcome,'' if msg.get('ok') else 'runner_failed_or_uncertain')
                await ws.send_json({'type':'result_ack','id':sid})
            else:raise ValueError('Unknown message')
    except WebSocketDisconnect:pass
    except Exception:
        await ws.close(code=4403)



class CustomerCreate(StrictRequest):
    display_name:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_DISPLAY_NAME_CHARS)
    external_ref:str=Field(default='',max_length=MAX_EXTERNAL_ID_CHARS)
    status:str='active'


class CustomerContact(StrictRequest):
    type:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_CHANNEL_CHARS)
    value:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_CONTACT_VALUE_CHARS)
    verified:bool=Field(default=False,strict=True)


class ChannelIdentity(StrictRequest):
    channel:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_CHANNEL_CHARS)
    external_id:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_EXTERNAL_ID_CHARS)
    verified:bool=Field(strict=True)


class CustomerOrder(StrictRequest):
    external_id:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_EXTERNAL_ID_CHARS)
    status:str='new'
    currency:str='UZS'
    total_minor:int=Field(default=MIN_MONEY_MINOR,ge=MIN_MONEY_MINOR,le=MAX_MONEY_MINOR,strict=True)


@router.get('/{tenant}/customers')
def customer_list(tenant:str,request:Request,query:str='',limit:int=100,offset:int=0):
    identity(request,tenant)
    from .customer360 import list_customers
    try:return {'customers':list_customers(tenant,query=query,limit=limit,offset=offset)}
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


@router.post('/{tenant}/customers')
def customer_create(tenant:str,req:CustomerCreate,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from .customer360 import create_customer
    try:
        out=create_customer(tenant,req.display_name,external_ref=req.external_ref,status=req.status,actor=who['sub'])
        return {'customer':out}
    except AuthenticationError:raise HTTPException(403,'Write permission denied')
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


@router.get('/{tenant}/customers/{customer_id}')
def customer_detail(tenant:str,customer_id:str,request:Request):
    identity(request,tenant)
    from .customer360 import CustomerNotFound,get_customer
    try:return {'customer':get_customer(tenant,customer_id)}
    except AuthenticationError:raise HTTPException(403,'Write permission denied')
    except CustomerNotFound:raise HTTPException(404,'Customer not found')
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


@router.post('/{tenant}/customers/{customer_id}/contacts')
def customer_contact(tenant:str,customer_id:str,req:CustomerContact,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from .customer360 import CustomerNotFound,add_contact
    try:return {'customer':add_contact(tenant,customer_id,req.type,req.value,verified=req.verified,actor=who['sub'])}
    except AuthenticationError:raise HTTPException(403,'Write permission denied')
    except CustomerNotFound:raise HTTPException(404,'Customer not found')
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


@router.post('/{tenant}/customers/{customer_id}/channel-identities')
def customer_channel_identity(tenant:str,customer_id:str,req:ChannelIdentity,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from .customer360 import CustomerNotFound,link_channel_identity
    try:return {'customer':link_channel_identity(tenant,customer_id,req.channel,req.external_id,verified=req.verified,actor=who['sub'])}
    except AuthenticationError:raise HTTPException(403,'Write permission denied')
    except CustomerNotFound:raise HTTPException(404,'Customer not found')
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


@router.post('/{tenant}/customers/{customer_id}/orders')
def customer_order(tenant:str,customer_id:str,req:CustomerOrder,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from .customer360 import CustomerNotFound,add_order
    try:
        return {'customer':add_order(tenant,customer_id,req.external_id,status=req.status,currency=req.currency,total_minor=req.total_minor,actor=who['sub'])}
    except AuthenticationError:raise HTTPException(403,'Write permission denied')
    except CustomerNotFound:raise HTTPException(404,'Customer not found')
    except (ValueError,LookupError) as exc:raise HTTPException(422,str(exc))


class ConnectionVerifyRequest(StrictRequest):
    agent:str | None=Field(default=None,min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)


@router.post('/{tenant}/connections/{connection_id}/verify')
def verify_connection(tenant:str,connection_id:str,request:Request,req:ConnectionVerifyRequest | None=None):
    who=identity(request,tenant,('owner','integrator'))
    from platform_runtime.connectors import probe_read_connection
    try:
        return probe_read_connection(engine(),tenant,connection_id,actor=who['sub'],
                                     agent=req.agent if req else None)
    except HTTPException:raise
    except (Forbidden, AuthenticationError):raise HTTPException(403,'Connector permission denied')
    except RuntimeError:raise HTTPException(503,'Connector dependency or provider unavailable')
    except (ValueError,LookupError,OSError) as exc:raise HTTPException(422,'Connector configuration invalid') from exc

@router.get('/{tenant}/connections')
def get_connections(tenant:str,request:Request):
    identity(request,tenant,('owner','integrator'))
    from platform_runtime.connectors import describe
    try:
        return {'connections':describe(tenant)}
    except Forbidden:
        raise HTTPException(403,'Connector unavailable')
    except (RuntimeError,ValueError,OSError):
        raise HTTPException(503,'Connector configuration unavailable')


class AgentRunRequest(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    key:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_KEY_CHARS)
    text:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_TEXT_CHARS)
    max_steps:int=Field(default=DEFAULT_AGENT_RUN_STEPS,ge=MIN_AGENT_RUN_STEPS,le=MAX_AGENT_RUN_STEPS)
    max_seconds:int=Field(default=DEFAULT_MAX_SECONDS,ge=MIN_MAX_SECONDS,le=MAX_MAX_SECONDS)


@router.post('/{tenant}/agent-runs')
def agent_run_create(tenant:str,req:AgentRunRequest,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from platform_runtime.agent_loop import AgentLoop
    run_id=call(AgentLoop(engine()).create,tenant,req.key,req.agent,req.text,who['sub'],
                max_steps=req.max_steps,max_seconds=req.max_seconds)
    return {'run_id':run_id,'accepted':True,'production_verified':False}


@router.get('/{tenant}/agent-runs')
def agent_run_list(tenant:str,request:Request):
    identity(request,tenant)
    from platform_runtime.agent_loop import AgentLoop
    return {'runs':call(AgentLoop(engine()).list,tenant)}


@router.get('/{tenant}/agent-runs/{run_id}')
def agent_run_detail(tenant:str,run_id:str,request:Request):
    identity(request,tenant)
    from platform_runtime.agent_loop import AgentLoop
    return call(AgentLoop(engine()).get,tenant,run_id)


@router.post('/{tenant}/agent-runs/{run_id}/cancel')
def agent_run_cancel(tenant:str,run_id:str,request:Request):
    who=identity(request,tenant,('owner','operator'))
    from platform_runtime.agent_loop import AgentLoop
    status=call(AgentLoop(engine()).cancel,tenant,run_id,who['sub'])
    return {'ok':True,'status':status}


# Development v0.3.6: local budget and knowledge service surfaces.
class BudgetSettings(StrictRequest):
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    limit_micro:int=Field(ge=MIN_LIMIT_MICRO,le=MAX_LIMIT_MICRO)
    max_inflight:int=Field(default=DEFAULT_MAX_INFLIGHT,ge=MIN_MAX_INFLIGHT,le=MAX_MAX_INFLIGHT)


class BudgetSettlement(StrictRequest):
    actual_micro:int=Field(ge=MIN_ACTUAL_MICRO,le=MAX_LIMIT_MICRO)
    evidence:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_EVIDENCE_CHARS)


@router.get('/{tenant}/usage-budget')
def budget_get(tenant:str,request:Request):
    from platform_runtime.usage_budget import UsageBudget
    identity(request,tenant,('owner','operator','integrator'))
    return call(UsageBudget(engine()).summary,tenant)


@router.put('/{tenant}/usage-budget')
def budget_set(tenant:str,req:BudgetSettings,request:Request):
    from platform_runtime.usage_budget import UsageBudget
    who=identity(request,tenant,('owner',))
    return call(UsageBudget(engine()).configure,tenant,who['sub'],req.currency,req.limit_micro,req.max_inflight)


@router.get('/{tenant}/usage-budget/pending')
def budget_pending(tenant:str,request:Request):
    from platform_runtime.usage_budget import UsageBudget
    identity(request,tenant,('owner',))
    return {'reservations':call(UsageBudget(engine()).pending,tenant)}


@router.post('/{tenant}/usage-budget/{reservation}/reconcile')
def budget_reconcile(tenant:str,reservation:str,req:BudgetSettlement,request:Request):
    from platform_runtime.usage_budget import UsageBudget
    who=identity(request,tenant,('owner',))
    call(UsageBudget(engine()).reconcile,tenant,reservation,who['sub'],req.actual_micro,req.evidence)
    return {'ok':True}


class KnowledgeCollection(StrictRequest):
    id:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    model:str=Field(default='',max_length=MAX_MODEL_CHARS)
    dimension:int=Field(default=MIN_DIMENSION,ge=MIN_DIMENSION,le=MAX_DIMENSION)


class KnowledgeGrant(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    allowed:bool


class KnowledgeDocument(StrictRequest):
    id:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    title:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_TITLE_CHARS)
    content:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_CONTENT_CHARS)
    source_url:str=Field(default='',max_length=MAX_URL_CHARS)
    expected_version:int=Field(default=MIN_VERSION,ge=MIN_VERSION,lt=MAX_VERSION)
    model:str=Field(default='',max_length=MAX_MODEL_CHARS)
    vectors:list[list[float|int]]|None=Field(default=None,max_length=MAX_VECTORS_PER_DOCUMENT)


class KnowledgeDelete(StrictRequest):
    expected_version:int=Field(ge=MIN_DELETE_VERSION,lt=MAX_VERSION)


@router.post('/{tenant}/knowledge/collections')
def knowledge_collection_create(tenant:str,req:KnowledgeCollection,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    who=identity(request,tenant,('owner',))
    return call(KnowledgeStore(engine()).create_collection,tenant,who['sub'],req.id,req.model,req.dimension)


@router.put('/{tenant}/knowledge/{collection}/grant')
def knowledge_grant(tenant:str,collection:str,req:KnowledgeGrant,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    who=identity(request,tenant,('owner',))
    call(KnowledgeStore(engine()).grant,tenant,who['sub'],collection,req.agent,req.allowed)
    return {'ok':True}


@router.get('/{tenant}/knowledge/{collection}/documents')
def knowledge_documents(tenant:str,collection:str,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    identity(request,tenant,('owner','operator','integrator'))
    return {'documents':call(KnowledgeStore(engine()).documents,tenant,collection)}


@router.put('/{tenant}/knowledge/{collection}/documents')
def knowledge_ingest(tenant:str,collection:str,req:KnowledgeDocument,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    who=identity(request,tenant,('owner','operator','integrator'))
    return call(KnowledgeStore(engine()).ingest,tenant,who['sub'],collection,req.id,req.title,req.content,
                req.expected_version,req.source_url,vectors=req.vectors,model=req.model)


@router.post('/{tenant}/knowledge/{collection}/documents/{document}/delete')
def knowledge_delete(tenant:str,collection:str,document:str,req:KnowledgeDelete,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    who=identity(request,tenant,('owner',))
    call(KnowledgeStore(engine()).delete,tenant,who['sub'],collection,document,req.expected_version)
    return {'ok':True}


class KnowledgeQuery(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    query:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_QUERY_CHARS)
    limit:int=Field(default=DEFAULT_KNOWLEDGE_RESULTS,ge=MIN_KNOWLEDGE_RESULTS,le=MAX_KNOWLEDGE_RESULTS)
    query_vector:list[float|int]|None=Field(default=None,max_length=MAX_VECTOR_DIMENSION)
    model:str=Field(default='',max_length=MAX_MODEL_CHARS)


@router.post('/{tenant}/knowledge/{collection}/search')
def knowledge_search(tenant:str,collection:str,req:KnowledgeQuery,request:Request):
    from platform_runtime.knowledge import KnowledgeStore
    identity(request,tenant,('owner','operator','integrator'))
    return call(KnowledgeStore(engine()).search,tenant,req.agent,collection,req.query,req.limit,
                query_vector=req.query_vector,model=req.model)


def reengagement():
    from platform_runtime.agent_loop import AgentLoop
    from platform_runtime.reengagement import ReengagementLoop
    e=engine()
    return ReengagementLoop(e,AgentLoop(e))


class ReengagementPolicy(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    connection:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    inactive_minutes:int=Field(default=DEFAULT_INACTIVE_MINUTES,ge=MIN_INACTIVE_MINUTES,le=MAX_INACTIVE_MINUTES,strict=True)
    cooldown_seconds:int=Field(default=DEFAULT_COOLDOWN_SECONDS,ge=MIN_COOLDOWN_SECONDS,le=MAX_COOLDOWN_SECONDS,strict=True)
    max_attempts:int=Field(default=DEFAULT_REENGAGEMENT_ATTEMPTS,ge=MIN_REENGAGEMENT_ATTEMPTS,le=MAX_REENGAGEMENT_ATTEMPTS,strict=True)
    max_per_cycle:int=Field(default=DEFAULT_REENGAGEMENT_PER_CYCLE,ge=MIN_REENGAGEMENT_PER_CYCLE,le=MAX_REENGAGEMENT_PER_CYCLE,strict=True)
    interval_seconds:int=Field(default=DEFAULT_CYCLE_INTERVAL_SECONDS,ge=MIN_CYCLE_INTERVAL_SECONDS,le=MAX_CYCLE_INTERVAL_SECONDS,strict=True)
    max_steps:int=Field(default=DEFAULT_REENGAGEMENT_STEPS,ge=MIN_AGENT_RUN_STEPS,le=MAX_AGENT_RUN_STEPS,strict=True)
    max_seconds:int=Field(default=DEFAULT_MAX_SECONDS,ge=MIN_MAX_SECONDS,le=MAX_MAX_SECONDS,strict=True)
    enabled:bool=Field(default=True,strict=True)


@router.get('/{tenant}/reengagement')
def reengagement_list(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    return {'policies':reengagement().policies(tenant)}


@router.put('/{tenant}/reengagement/{policy}')
def reengagement_configure(tenant:str,policy:str,req:ReengagementPolicy,request:Request):
    # Owner-only: this schedules automated outreach to real customers.
    who=identity(request,tenant,('owner',))
    settings=req.model_dump()
    agent=settings.pop('agent');connection=settings.pop('connection')
    return call(reengagement().configure,tenant,policy,agent,connection,who['sub'],**settings)


@router.get('/{tenant}/reengagement/{policy}/ledger')
def reengagement_ledger(tenant:str,policy:str,request:Request,limit:int=100):
    identity(request,tenant,('owner','operator'))
    entries,total,truncated=reengagement().ledger(tenant,policy,limit,with_total=True)
    return {'policy':policy,'entries':entries,'total':total,'truncated':truncated}


@router.post('/{tenant}/reengagement/{policy}/sync')
def reengagement_sync(tenant:str,policy:str,request:Request):
    identity(request,tenant,('owner',))
    return {'changed':reengagement().sync_ledger(tenant,policy)}


def briefing():
    from platform_runtime.briefing import Briefing
    return Briefing(engine())


class BriefingSchedule(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    recipient:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    connection:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    sections:list[dict]=Field(min_length=MIN_SECTIONS,max_length=MAX_SECTIONS)
    interval_seconds:int=Field(default=DEFAULT_COOLDOWN_SECONDS,ge=MIN_CYCLE_INTERVAL_SECONDS,le=MAX_CYCLE_INTERVAL_SECONDS,strict=True)
    hour:int=Field(default=DEFAULT_HOUR,ge=MIN_HOUR,le=MAX_HOUR,strict=True)
    minute:int=Field(default=DEFAULT_MINUTE,ge=MIN_MINUTE,le=MAX_MINUTE,strict=True)
    timezone_offset_minutes:int=Field(default=DEFAULT_TIMEZONE_OFFSET_MINUTES,ge=MIN_TIMEZONE_OFFSET_MINUTES,le=MAX_TIMEZONE_OFFSET_MINUTES,strict=True)
    max_sections:int=Field(default=DEFAULT_MAX_SECTIONS,ge=MIN_SECTIONS,le=MAX_SECTIONS,strict=True)
    max_rows:int=Field(default=DEFAULT_BRIEFING_ROWS,ge=MIN_BRIEFING_ROWS,le=MAX_BRIEFING_ROWS,strict=True)
    max_seconds:int=Field(default=DEFAULT_MAX_SECONDS,ge=MIN_MAX_SECONDS,le=MAX_MAX_SECONDS,strict=True)
    title:str=Field(default='',max_length=MAX_BRIEFING_TITLE_CHARS)
    enabled:bool=Field(default=True,strict=True)


@router.get('/{tenant}/briefing')
def briefing_list(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    return {'schedules':briefing().schedules(tenant)}


@router.put('/{tenant}/briefing/{schedule}')
def briefing_configure(tenant:str,schedule:str,req:BriefingSchedule,request:Request):
    # Owner-only: this schedules automated delivery to a real chat.
    who=identity(request,tenant,('owner',))
    settings=req.model_dump()
    agent=settings.pop('agent');recipient=settings.pop('recipient')
    connection=settings.pop('connection')
    return call(briefing().configure,tenant,schedule,agent,recipient,connection,who['sub'],
                **settings)


@router.get('/{tenant}/briefing/{schedule}/ledger')
def briefing_ledger(tenant:str,schedule:str,request:Request,limit:int=100):
    identity(request,tenant,('owner','operator'))
    entries,total,truncated=briefing().ledger(tenant,schedule,limit,with_total=True)
    return {'schedule':schedule,'entries':entries,'total':total,'truncated':truncated}


def escalation():
    from platform_runtime.escalation import EscalationLoop
    return EscalationLoop(engine())


class EscalationSchedule(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    recipient:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    title:str=Field(default='',max_length=MAX_BRIEFING_TITLE_CHARS)
    cooldown_seconds:int=Field(default=DEFAULT_COOLDOWN_SECONDS,ge=MIN_COOLDOWN_SECONDS,le=MAX_COOLDOWN_SECONDS,strict=True)
    max_per_cycle:int=Field(default=DEFAULT_ESCALATION_PER_CYCLE,ge=MIN_ESCALATION_PER_CYCLE,le=MAX_ESCALATION_PER_CYCLE,strict=True)
    interval_seconds:int=Field(default=DEFAULT_CYCLE_INTERVAL_SECONDS,ge=MIN_CYCLE_INTERVAL_SECONDS,le=MAX_CYCLE_INTERVAL_SECONDS,strict=True)
    max_age_days:int=Field(default=DEFAULT_MAX_AGE_DAYS,ge=MIN_MAX_AGE_DAYS,le=MAX_MAX_AGE_DAYS,strict=True)
    enabled:bool=Field(default=True,strict=True)


@router.get('/{tenant}/escalation')
def escalation_list(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    return {'schedules':escalation().schedules(tenant)}


@router.put('/{tenant}/escalation/{schedule}')
def escalation_configure(tenant:str,schedule:str,req:EscalationSchedule,request:Request):
    # Owner-only: this schedules automated escalation about real people to a
    # real chat. A non-owner must not be able to point it at a manager.
    who=identity(request,tenant,('owner',))
    settings=req.model_dump()
    agent=settings.pop('agent');recipient=settings.pop('recipient')
    return call(escalation().configure,tenant,schedule,agent,recipient,who['sub'],**settings)


@router.get('/{tenant}/escalation/{schedule}/ledger')
def escalation_ledger(tenant:str,schedule:str,request:Request,limit:int=100):
    identity(request,tenant,('owner','operator'))
    entries,total,truncated=escalation().ledger(tenant,schedule,limit,with_total=True)
    return {'schedule':schedule,'entries':entries,'total':total,'truncated':truncated}


@router.post('/{tenant}/escalation/{schedule}/disable')
def escalation_disable(tenant:str,schedule:str,request:Request,reason:str='operator_disabled'):
    who=identity(request,tenant,('owner',))
    return call(escalation().disable,tenant,schedule,who['sub'],reason)


def supervisor():
    from platform_runtime.supervisor import Supervisor
    return Supervisor(engine())


class SupervisorSection(StrictRequest):
    agent:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_IDENTIFIER_CHARS)
    title:str=Field(default='',max_length=MAX_BRIEFING_TITLE_CHARS)
    keywords:list[str]=Field(default_factory=list,max_length=MAX_KEYWORDS_PER_SECTION)
    enabled:bool=Field(default=True,strict=True)


class SupervisorRoute(StrictRequest):
    question:str=Field(min_length=MIN_NON_EMPTY,max_length=MAX_QUESTION_CHARS)
    section:str=Field(default='',max_length=MAX_SECTION_REF_CHARS)
    max_hops:int=Field(default=MIN_SUPERVISOR_HOPS,ge=MIN_SUPERVISOR_HOPS,le=MAX_SUPERVISOR_HOPS,strict=True)
    max_steps:int=Field(default=DEFAULT_SUPERVISOR_STEPS,ge=MIN_AGENT_RUN_STEPS,le=MAX_AGENT_RUN_STEPS,strict=True)
    max_seconds:int=Field(default=DEFAULT_MAX_SECONDS,ge=MIN_MAX_SECONDS,le=MAX_MAX_SECONDS,strict=True)


@router.get('/{tenant}/supervisor')
def supervisor_sections(tenant:str,request:Request):
    identity(request,tenant,('owner','operator'))
    return {'sections':supervisor().sections(tenant)}


@router.put('/{tenant}/supervisor/{section}')
def supervisor_declare(tenant:str,section:str,req:SupervisorSection,request:Request):
    # Owner-only: declaring a section decides which agent answers a manager's
    # question, so it is a routing-authority change, not a preference.
    who=identity(request,tenant,('owner',))
    settings=req.model_dump()
    agent=settings.pop('agent')
    return call(supervisor().declare,tenant,section,agent,who['sub'],**settings)


@router.post('/{tenant}/supervisor/route')
def supervisor_route(tenant:str,req:SupervisorRoute,request:Request):
    # Owner/operator: routing spends a real agent run, so it is a normal
    # operational action. The hop cap is enforced in code, not by this default.
    who=identity(request,tenant,('owner','operator'))
    settings=req.model_dump()
    question=settings.pop('question')
    key=request.headers.get('Idempotency-Key','')
    if not key:
        raise HTTPException(422,'Idempotency-Key header required')
    return call(supervisor().route,tenant,key,question,who['sub'],**settings)


@router.get('/{tenant}/supervisor/history')
def supervisor_history(tenant:str,request:Request,limit:int=100):
    identity(request,tenant,('owner','operator'))
    routes,total,truncated=supervisor().history(tenant,limit,with_total=True)
    return {'routes':routes,'total':total,'truncated':truncated}

