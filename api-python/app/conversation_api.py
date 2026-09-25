"""Operator takeover reply: a human answers a customer conversation from the dashboard.

POST /platform/{tenant}/conversations/{channel}/{conversation_id}/reply
  body {"text": 1..4000 characters}, header Idempotency-Key (required),
  roles owner/operator -> {"task_id": str, "status": str}

This layer authenticates and routes; it authorises nothing on its own.
Engine.operator_reply decides whether the send may exist -- the channel owns a
verified inbound stream, the conversation has written to this tenant on it, the
actor holds the role now, the tenant is not frozen -- and the claim path decides
it again before dispatch. The owning agent is resolved by capability (the first
pack agent holding ``<channel>.send``), never named here.

Status mapping is platform_api.call's: 401/403 identity and policy, 404 unknown
conversation, 409 same key with a different body, 422 input, missing key, a
channel that cannot carry a reply, or no agent holding its send tool.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from platform_runtime.conversation import start_takeover
from platform_runtime.engine import OPERATOR_ROLES, OUTBOUND_CHANNELS
from . import platform_api as api
from .packs import PackError
from .planning import route

router = APIRouter(prefix='/platform', tags=['conversations'])


class Reply(api.StrictRequest):
    text: str = Field(min_length=api.MIN_NON_EMPTY, max_length=api.MAX_TEXT_CHARS)


@router.post('/{tenant}/conversations/{channel}/{conversation_id}/reply')
def reply(tenant: str, channel: str, conversation_id: str, req: Reply, request: Request):
    who = api.identity(request, tenant, tuple(sorted(OPERATOR_ROLES)))
    key = request.headers.get('Idempotency-Key', '')
    if not key:
        raise HTTPException(422, 'Idempotency-Key header required')
    if channel not in OUTBOUND_CHANNELS:
        raise HTTPException(422, 'Channel cannot carry an operator reply')
    try:
        agent = api.call(route, api.agents, tenant, channel + '.send')
    except PackError:
        raise HTTPException(422, 'Tenant pack unavailable') from None
    e = api.engine()
    task_id = api.call(e.operator_reply, tenant, channel, conversation_id, req.text,
                       actor=who['sub'], role=who['role'], key=key, agent=agent)
    # A human now holds this chat: the bot opens no run for it for the agent's
    # conversation.takeover_minutes (platform_runtime.conversation.start_takeover).
    api.call(start_takeover, e, tenant, channel, conversation_id, who['sub'], agent)
    return {'task_id': task_id, 'status': api.call(e.get, tenant, task_id)['status']}
