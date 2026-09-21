"""Explicit local-loopback mode for OpenAI-compatible local inference.

Only numeric loopback HTTP endpoints can omit provider authentication. The
cloud default remains HTTPS + a configured API secret. No NotebookLM hosting.
"""
import json
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Model redirect denied')


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


def headers(cfg,resolve_secret):
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
        except Exception:
            raise RuntimeError('Local model request failed') from None
    return send
