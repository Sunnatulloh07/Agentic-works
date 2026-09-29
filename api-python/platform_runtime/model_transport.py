"""Provider dialects and the explicit local-loopback mode.

Two request shapes leave this platform. ``openai`` is the historical default and
is unchanged byte for byte. ``anthropic`` is the Messages API, whose wire shape
is taken from the bundled ``claude-api`` skill, ``curl/examples.md``:

    curl https://api.anthropic.com/v1/messages \\
      -H "Content-Type: application/json" \\
      -H "x-api-key: $ANTHROPIC_API_KEY" \\
      -H "anthropic-version: 2023-06-01" \\
      -d '{"model": ..., "max_tokens": ..., "messages": [{"role": "user", ...}]}'

Only numeric loopback HTTP endpoints can omit provider authentication. The
cloud default remains HTTPS + a configured API secret. No NotebookLM hosting.

Failed calls are classified once, here, for two readers: the retry loop
(``with_retries``) and the spending ledger (``usage_budget.metered_completion``).
A request that never left (``NOT_SENT``) or that the provider answered with an
HTTP error status (``REJECTED``) was not billed; a request that left and got no
complete answer -- a read timeout, a reset -- is ``UNKNOWN``. Skill,
shared/error-codes.md: 429, 500 and 529 are retryable, 400/401/402/403/404/413
are not, and the SDKs retry 429/5xx twice with exponential backoff; 429 carries
``retry-after`` in seconds.
"""
import http.client
import json
import math
import random
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .tools import MAX_SCHEMA_DEPTH, post_json

# A local inference endpoint is operator-configured but still an untrusted peer: it can
# be a hostile process on the same host. The port floor keeps the platform from being
# pointed at a privileged service, and the byte limits bound what a bad endpoint can
# make this process allocate. All four were literals with nothing asserting them.
MIN_LOCAL_PORT = 1024
MAX_PORT = 65535
MAX_LOCAL_REQUEST_BYTES = 128000
MAX_LOCAL_RESPONSE_BYTES = 128000
LOCAL_TIMEOUT_SECONDS = 30

SUPPORTED_PROVIDERS = frozenset({'openai', 'anthropic'})
OPENAI_BASE_URL = 'https://api.openai.com/v1'
OPENAI_PATH = '/chat/completions'
# Skill, curl/examples.md -> Required Headers: `anthropic-version` value `2023-06-01`.
# It is the API version, not a model version, and is required on every request.
ANTHROPIC_BASE_URL = 'https://api.anthropic.com'
ANTHROPIC_VERSION = '2023-06-01'
ANTHROPIC_PATH = '/v1/messages'
# Skill, curl/examples.md -> Thinking: current Claude models think by default and
# thinking tokens count toward max_tokens, so a small planner ceiling can end the
# turn at `max_tokens` before any text block. 16000 is the skill's non-streaming
# default. It is a ceiling, not a charge: billing follows tokens actually produced.
ANTHROPIC_MIN_MAX_TOKENS = 16000
# Skill, Thinking & Effort: `output_config.effort`, GA, no beta header. Sent only
# when configured: Haiku 4.5 rejects the field.
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')

# How the result-fed planner receives a decision (`llm.protocol`). `json` is the
# historical text protocol and the default; `tools` is Claude's native tool use
# (skill, curl/examples.md -> Tool Use), Anthropic dialect only.
PLANNER_PROTOCOLS = ('json', 'tools')
# Skill, shared/tool-use-concepts.md -> Tool Choice Options: forced `any`/`tool`
# is a 400 on Claude Opus 5.5 and the 5.1 models, so `auto`. With
# `disable_parallel_tool_use` a turn carries at most one call, and one planner
# call is one decision.
TOOL_CHOICE = {'type': 'auto', 'disable_parallel_tool_use': True}
# The bundled skill does not state the tool-name pattern. Registry names contain
# dots, so they are mapped ('.' -> '__') into a deliberately conservative
# subset -- ASCII letters, digits, '_' and '-', at most 64 -- that is valid
# under any pattern the API has used.
TOOL_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
# Skill, shared/tool-use-concepts.md -> Structured Outputs: strict schemas take
# type/enum and `additionalProperties: false` on every object, but no string,
# number or array length constraints. The SDKs strip those and validate them
# client-side; here the engine's own validation (tools.validate_schema) is that
# client-side check, so the projection keeps only these keys.
STRICT_SCHEMA_KEYS = ('type', 'description', 'enum', 'properties', 'required', 'items')

# A model call has its own timeout. It used to share tools.PROVIDER_TIMEOUT_SECONDS
# (25 s), which a thinking model answering a non-streaming request can exceed.
# `llm.timeout_seconds` sets it per tenant. urllib applies it per socket wait; a
# non-streaming reply sends nothing until it is complete, so it bounds the wait.
DEFAULT_MODEL_TIMEOUT_SECONDS = 60
MIN_MODEL_TIMEOUT_SECONDS = 10
MAX_MODEL_TIMEOUT_SECONDS = 120
# One call is at most three attempts (two retries, the SDK default), and never
# starts an attempt that could end after MODEL_CALL_DEADLINE_SECONDS: all
# attempts and waits together. agent_loop.PLANNER_LEASE_SECONDS is longer than
# this, so a result is never discarded for arriving after its own lease.
MAX_MODEL_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
MAX_RETRY_WAIT_SECONDS = 8.0
MODEL_CALL_DEADLINE_SECONDS = 150
# 408 request timeout and 409 conflict are transient by definition; 429 and 5xx
# per the skill. Every other 4xx will fail again the same way.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 409, 429})
NOT_SENT = 'not_sent'
REJECTED = 'rejected'
UNKNOWN = 'unknown'


class ModelRequestFailed(RuntimeError):
    """A classified model-call failure. Carries no URL: a URL can carry a token.

    ``outcome`` is NOT_SENT, REJECTED or UNKNOWN; ``status`` the HTTP status of a
    rejection; ``retry_after`` the provider's requested wait in seconds, if any.
    """
    def __init__(self, outcome, status=None, retry_after=None):
        self.outcome, self.status, self.retry_after = outcome, status, retry_after
        super().__init__('Model request failed: ' + outcome
                         + (' http_%d' % status if status is not None else ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Model redirect denied')


class LocalRequestRejected(RuntimeError):
    """The loopback peer answered with an HTTP error status.

    Still the RuntimeError every caller of ``transport_for`` already catches; the
    status is carried as a number so an adapter that needs to tell a definite
    rejection (4xx) from an unknown outcome (5xx) can, without the URL -- which,
    for the Telegram transport, contains the bot token.
    """
    def __init__(self, code):
        self.code = int(code)
        super().__init__(f'Local request rejected: http_{self.code}')


def local_mode(cfg):
    mode=cfg.get('provider_mode','cloud')
    if mode not in {'cloud','local_loopback'}:raise ValueError('Unknown model provider mode')
    return mode=='local_loopback'


def validate_url(cfg,url):
    parts=urlsplit(url)
    if not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
        raise ValueError('Plain model provider URL required')
    if local_mode(cfg):
        if parts.scheme!='http' or parts.hostname not in {'127.0.0.1','::1'} or parts.port is None:
            raise ValueError('Local model requires explicit numeric loopback HTTP and port')
        if not MIN_LOCAL_PORT<=parts.port<=MAX_PORT:raise ValueError('Local model requires an unprivileged service port')
    elif parts.scheme!='https':
        raise ValueError('Cloud model HTTPS required')


def provider(cfg):
    name=cfg.get('provider','openai')
    if name not in SUPPORTED_PROVIDERS:raise ValueError('Unknown model provider')
    return name


def planner_protocol(cfg):
    """`protocol` of the llm block: 'json' (default) or 'tools' (anthropic only).

    Read by the result-fed planner before any reservation or request, so a
    misconfiguration fails the call without spending anything.
    """
    name=provider(cfg)
    value=cfg.get('protocol','json')
    if not isinstance(value,str) or value not in PLANNER_PROTOCOLS:
        raise ValueError('Model protocol must be one of '+', '.join(PLANNER_PROTOCOLS))
    if value=='tools' and name!='anthropic':
        raise ValueError('Model protocol tools requires provider anthropic')
    return value


def model_timeout(cfg):
    """`timeout_seconds` of the llm block: an integer 10..120, default 60."""
    if 'timeout_seconds' not in cfg:return DEFAULT_MODEL_TIMEOUT_SECONDS
    value=cfg['timeout_seconds']
    if type(value) is not int or not MIN_MODEL_TIMEOUT_SECONDS<=value<=MAX_MODEL_TIMEOUT_SECONDS:
        raise ValueError('Model timeout_seconds must be an integer %d..%d'%(MIN_MODEL_TIMEOUT_SECONDS,MAX_MODEL_TIMEOUT_SECONDS))
    return value


def _retry_after(headers):
    try:value=float((headers or {}).get('retry-after'))
    except (TypeError,ValueError):return None  # absent, or an HTTP date: back off instead
    return value if math.isfinite(value) and value>=0 else None


def classify(exc):
    """A transport exception -> ModelRequestFailed, or None when it is not a transport failure.

    urllib wraps every failure while connecting or SENDING in URLError, so a
    URLError that is not an HTTPError means the request never fully left. A
    timeout, reset or broken response raised outside that wrapper happened while
    WAITING for the answer: the provider may have run, and billed, the request.
    """
    if isinstance(exc,ModelRequestFailed):return exc
    if isinstance(exc,LocalRequestRejected):return ModelRequestFailed(REJECTED,exc.code)
    if isinstance(exc,urllib.error.HTTPError):return ModelRequestFailed(REJECTED,exc.code,_retry_after(exc.headers))
    if isinstance(exc,urllib.error.URLError):return ModelRequestFailed(NOT_SENT)
    if isinstance(exc,(TimeoutError,ConnectionError,http.client.HTTPException)):return ModelRequestFailed(UNKNOWN)
    return None


def retry_wait(failure,attempt,jitter=random.random):
    """Seconds to wait before retrying after `failure` on attempt index `attempt`, or None."""
    if failure.outcome==REJECTED and not (failure.status in RETRYABLE_CLIENT_STATUSES or 500<=failure.status<=599):
        return None
    backoff=min(MAX_RETRY_WAIT_SECONDS,BACKOFF_BASE_SECONDS*2**attempt)*(1-0.25*jitter())
    if failure.retry_after is None:return backoff
    # A provider asking for longer than we would wait will refuse an earlier retry.
    return None if failure.retry_after>MAX_RETRY_WAIT_SECONDS else max(backoff,failure.retry_after)


def with_retries(attempt,*,timeout,deadline=MODEL_CALL_DEADLINE_SECONDS,
                 sleep=time.sleep,clock=time.monotonic,jitter=random.random):
    """Call ``attempt(index)`` until it returns, at most MAX_MODEL_ATTEMPTS times.

    Only a classified, retryable failure is retried, and only when the wait plus
    one more full ``timeout`` still ends within ``deadline`` of the start. The
    last failure is raised as a ModelRequestFailed with no chained cause; an
    unclassified exception propagates unchanged on its first occurrence.
    """
    started=clock()
    for index in range(MAX_MODEL_ATTEMPTS):
        try:
            return attempt(index)
        except Exception as exc:
            failure=classify(exc)
            if failure is None:raise
            wait=None if index+1>=MAX_MODEL_ATTEMPTS else retry_wait(failure,index,jitter)
            if wait is None or clock()-started+wait+timeout>deadline:
                raise failure from None
        sleep(wait)


def completion_url(cfg):
    """The single-completion endpoint of the configured dialect."""
    if provider(cfg)=='anthropic':
        return str(cfg.get('base_url',ANTHROPIC_BASE_URL)).rstrip('/')+ANTHROPIC_PATH
    return str(cfg.get('base_url',OPENAI_BASE_URL)).rstrip('/')+OPENAI_PATH


def completion_body(cfg,model,system,user_text,max_tokens):
    """The request body of the configured dialect. The prompt text is identical.

    Anthropic takes the system prompt as a TOP-LEVEL `system` field, not as a
    `messages` entry with role `system` (skill, curl/examples.md -> Prompt
    Caching, which sends `"system": [...]` alongside `"messages"`). Two OpenAI
    fields are deliberately absent rather than translated:

    * `temperature` -- removed on the current Claude models; the skill's
      Thinking & Effort table records it as "Removed - 400" for Opus 5, Opus
      4.8/4.7, Sonnet 5 and the Fable family, so sending it would fail the call.
    * `response_format` -- the OpenAI JSON-object switch has no Anthropic
      counterpart. Anthropic's equivalent is structured outputs
      (`output_config.format` with a JSON schema), and the skill's raw-HTTP
      document gives no `output_config.format` example -- only
      `output_config.effort` -- so this adapter does NOT guess that wire shape.
      Strict JSON comes from the system prompt, which already demands exactly
      one JSON object, and from `parse_anthropic_decision`, which refuses
      anything else. The engine re-validates the decision afterwards either way.

    The Anthropic system prompt is one text block marked
    ``cache_control: {"type": "ephemeral"}`` -- skill, curl/examples.md -> Prompt
    Caching, and shared/prompt-caching.md -> "Large system prompt shared across
    many requests". Both planners keep their system prompt byte-stable per agent
    (no timestamps, ids or per-turn text) and put everything per-turn in the user
    message after the breakpoint. A prefix below the model's minimum cacheable
    length silently is not cached; nothing fails.
    """
    effort=_effort(cfg)
    if provider(cfg)=='anthropic':
        body={'model':model,'max_tokens':max(max_tokens,ANTHROPIC_MIN_MAX_TOKENS),
              'system':[{'type':'text','text':system,'cache_control':{'type':'ephemeral'}}],
              'messages':[{'role':'user','content':user_text}]}
        if effort is not None:body['output_config']={'effort':effort}
        return body
    if effort is not None:raise ValueError('Model effort is an anthropic provider setting')
    return {'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user_text}],
            'temperature':0,'max_tokens':max_tokens,'response_format':{'type':'json_object'}}


def _effort(cfg):
    effort=cfg.get('effort')
    if 'effort' in cfg and (not isinstance(effort,str) or effort not in EFFORT_LEVELS):
        raise ValueError('Model effort must be one of '+', '.join(EFFORT_LEVELS))
    return effort


def tool_name(name):
    """A registry tool name -> its name on the wire ('products.search' -> 'products__search')."""
    if not isinstance(name,str):raise ValueError('Tool name must be a string')
    wire=name.replace('.','__')
    if not TOOL_NAME.fullmatch(wire):raise ValueError('Tool name outside the provider charset')
    return wire


def strict_schema(schema,depth=0):
    """A registry argument schema -> its strict-tool projection. Never mutates the input.

    Every object is closed (`additionalProperties: false`), as strict mode
    requires; length and range constraints are dropped because strict mode
    rejects them. The engine re-validates the full schema before anything runs.
    """
    if depth>MAX_SCHEMA_DEPTH:raise ValueError('Schema nesting too deep')
    if not isinstance(schema,dict):raise ValueError('Tool schema must be an object')
    out={key:schema[key] for key in STRICT_SCHEMA_KEYS if key in schema and key not in ('properties','items')}
    if 'required' in out:out['required']=list(out['required'])
    if 'enum' in out:out['enum']=list(out['enum'])
    if schema.get('type')=='object':
        out['properties']={key:strict_schema(value,depth+1) for key,value in schema.get('properties',{}).items()}
        out['additionalProperties']=False
    if 'items' in schema:out['items']=strict_schema(schema['items'],depth+1)
    return out


def tool_definition(name,description,schema):
    """One strict client tool: skill, curl/examples.md -> Tool Use, plus `strict`."""
    return {'name':name,'description':description,'strict':True,'input_schema':strict_schema(schema)}


def tools_completion_body(cfg,model,system,messages,tools,max_tokens):
    """The Messages API body of the `tools` protocol.

    Tools render before the system prompt, so the system block's breakpoint
    caches both (skill, shared/prompt-caching.md: render order tools -> system
    -> messages). The caller places the one message breakpoint.
    """
    if planner_protocol(cfg)!='tools':raise ValueError('Tool-use body requires protocol tools')
    effort=_effort(cfg)
    body={'model':model,'max_tokens':max(max_tokens,ANTHROPIC_MIN_MAX_TOKENS),
          'system':[{'type':'text','text':system,'cache_control':{'type':'ephemeral'}}],
          'tools':tools,'tool_choice':dict(TOOL_CHOICE),'messages':messages}
    if effort is not None:body['output_config']={'effort':effort}
    return body


def headers(cfg,resolve_secret):
    if provider(cfg)=='anthropic':
        # `x-api-key`, never `Authorization: Bearer` -- skill, curl/examples.md
        # -> Required Headers. Content-Type is added by the transport itself.
        version={'anthropic-version':ANTHROPIC_VERSION}
        if local_mode(cfg) and not cfg.get('key_env'):
            return version
        return {'x-api-key':resolve_secret(cfg,'key_env'),**version}
    if local_mode(cfg) and not cfg.get('key_env'):
        return {}
    return {'Authorization':'Bearer '+resolve_secret(cfg,'key_env')}


def transport_for(cfg,transport=post_json,timeout=None):
    """The sender for `cfg`. `timeout` is the model's (model_timeout); None keeps
    each transport's own default, which is what the Telegram loopback send uses."""
    def send(url,body,request_headers):
        validate_url(cfg,url)
        if not local_mode(cfg) or transport is not post_json:
            if timeout is not None and transport is post_json:
                return transport(url,body,request_headers,timeout=timeout)
            return transport(url,body,request_headers)
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(data)>MAX_LOCAL_REQUEST_BYTES:raise ValueError('Local model request exceeds byte limit')
        request=urllib.request.Request(url,data,{'Content-Type':'application/json',**request_headers},method='POST')
        # Local inference on the deployed machine must never route prompts through
        # a configured outbound proxy. No network is used by offline tests.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            with opener.open(request,timeout=LOCAL_TIMEOUT_SECONDS if timeout is None else timeout) as response:
                if response.status!=200:raise RuntimeError('Local model request rejected')
                raw=response.read(MAX_LOCAL_RESPONSE_BYTES+1)
                if len(raw)>MAX_LOCAL_RESPONSE_BYTES:raise ValueError('Local model response exceeds byte limit')
                from .model_response import unique_object, reject_constant
                return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except urllib.error.HTTPError as error:
            # Status only: the error object carries the URL.
            raise LocalRequestRejected(error.code) from None
        except Exception as error:
            # Still a RuntimeError for every caller; the class is kept so the
            # ledger and the retry loop can tell "never left" from "no answer".
            failure=classify(error)
            if failure is not None:raise failure from None
            raise RuntimeError('Local model request failed') from None
    return send
