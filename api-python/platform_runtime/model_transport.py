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
"""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .tools import post_json

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
    """
    effort=cfg.get('effort')
    if 'effort' in cfg and (not isinstance(effort,str) or effort not in EFFORT_LEVELS):
        raise ValueError('Model effort must be one of '+', '.join(EFFORT_LEVELS))
    if provider(cfg)=='anthropic':
        body={'model':model,'max_tokens':max(max_tokens,ANTHROPIC_MIN_MAX_TOKENS),'system':system,
              'messages':[{'role':'user','content':user_text}]}
        if effort is not None:body['output_config']={'effort':effort}
        return body
    if effort is not None:raise ValueError('Model effort is an anthropic provider setting')
    return {'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user_text}],
            'temperature':0,'max_tokens':max_tokens,'response_format':{'type':'json_object'}}


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


def transport_for(cfg,transport=post_json):
    def send(url,body,request_headers):
        validate_url(cfg,url)
        if not local_mode(cfg) or transport is not post_json:
            return transport(url,body,request_headers)
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(data)>MAX_LOCAL_REQUEST_BYTES:raise ValueError('Local model request exceeds byte limit')
        request=urllib.request.Request(url,data,{'Content-Type':'application/json',**request_headers},method='POST')
        # Local inference on the deployed machine must never route prompts through
        # a configured outbound proxy. No network is used by offline tests.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            with opener.open(request,timeout=LOCAL_TIMEOUT_SECONDS) as response:
                if response.status!=200:raise RuntimeError('Local model request rejected')
                raw=response.read(MAX_LOCAL_RESPONSE_BYTES+1)
                if len(raw)>MAX_LOCAL_RESPONSE_BYTES:raise ValueError('Local model response exceeds byte limit')
                from .model_response import unique_object, reject_constant
                return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except urllib.error.HTTPError as error:
            # Status only: the error object carries the URL.
            raise LocalRequestRejected(error.code) from None
        except Exception:
            raise RuntimeError('Local model request failed') from None
    return send
