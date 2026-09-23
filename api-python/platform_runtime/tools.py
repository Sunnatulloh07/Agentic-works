"""Executable allowlisted tools. All schemas reject unknown keys.

No tool takes a caller-supplied URL or secret. Network destinations are operator config.
Provider failures after writes are uncertain and are NEVER blindly retried.
"""
import functools
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from .engine import encode, Conflict

# The bounds this module enforces, named. A scan selected this module because it
# carried thirteen large numeric literals and NO named constant at all, so a bound
# could not be referred to, grepped for, or pinned by name -- every one of them had
# to be counted by hand. Naming them changes nothing at runtime and is the
# precondition for measuring them.
MAX_SCHEMA_DEPTH = 64
MAX_ARGUMENT_BYTES = 20000
DEFAULT_MAX_ITEMS = 100
DEFAULT_STRING_LENGTH = 4000
INTEGER_BOUND = 10 ** 12
MAX_PROVIDER_RESPONSE = 1_000_000
PROVIDER_TIMEOUT_SECONDS = 25
MEMORY_SEARCH_LIMIT = 10
RECORDS_LIST_LIMIT = 50
CREDENTIAL_NAME = re.compile(r'[A-Z][A-Z0-9_]*')
RISK_LEVELS = frozenset({'read', 'write', 'destructive', 'physical'})
# A tenant's own configuration lives in its pack directory. The charset and the
# default directory are app/packs.py's, restated rather than imported: the
# runtime may not depend on the API layer, so the duplication is the boundary.
TENANT_NAME = re.compile(r'[A-Za-z0-9_-]+')
PACK_INTEGRATIONS_FILE = 'integrations.yaml'
DEFAULT_PACKS_DIR = Path(__file__).resolve().parents[2] / 'packs'


def validate_schema(value,schema,depth=0):
    # A schema is operator configuration, so its DEPTH is bounded by nothing this
    # module controls, and this function recurses once per level. Measured: a
    # 1500-level schema raised RecursionError -- the guard CRASHED on the input it
    # exists to refuse, and RecursionError is not the ValueError contract every
    # caller catches. Reachable, not theoretical: `mcp.call` validates caller
    # arguments against an operator-declared schema, and `arguments_json` is
    # allowed 12000 characters, which is more than the ~10500 a 1500-level value
    # needs. The deepest schema in the 88-tool registry is 3, so this ceiling is
    # generous rather than tight.
    if depth>MAX_SCHEMA_DEPTH:raise ValueError('Schema nesting too deep')
    typ=schema.get('type')
    allowed={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool}
    if typ not in allowed or not isinstance(value,allowed[typ]) or (typ=='integer' and isinstance(value,bool)):
        raise ValueError('Schema type mismatch')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('Value outside enum')
    if typ=='object':
        # BYTES, not characters. Every other bound on a serialised payload in
        # this runtime measures bytes (database.contract.parse_request 12000,
        # erp._observations 12000/48000, the managed backends 1024/16000/16000),
        # and this one measured characters, so a non-ASCII argument passed at
        # roughly TWICE the declared size. Measured: a Cyrillic object under
        # 20 000 characters whose JSON is over 20 000 bytes was accepted.
        if len(encode(value).encode('utf-8'))>MAX_ARGUMENT_BYTES:raise ValueError('Arguments too large')
        props=schema.get('properties',{})
        if set(value)-set(props) or set(schema.get('required',[]))-set(value):raise ValueError('Schema fields mismatch')
        for k,v in value.items():validate_schema(v,props[k],depth+1)
    if typ=='array':
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',DEFAULT_MAX_ITEMS):raise ValueError('Array length')
        for item in value:validate_schema(item,schema['items'],depth+1)
    if typ=='string':
        if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',DEFAULT_STRING_LENGTH):raise ValueError('String length')
    if typ=='integer' and not schema.get('minimum',-INTEGER_BOUND)<=value<=schema.get('maximum',INTEGER_BOUND):raise ValueError('Integer bounds')


def obj(props,required=None):return {'type':'object','properties':props,'required':list(props) if required is None else required,'additionalProperties':False}
def string(maximum=DEFAULT_STRING_LENGTH):return {'type':'string','minLength':1,'maxLength':maximum}


@dataclass(frozen=True)
class Tool:
    name:str
    risk:str
    schema:dict
    handler:object=None
    external:bool=False
    runner:bool=False
    def validate(self,args):validate_schema(args,self.schema)


class Registry:
    def __init__(self):self.items={}
    def add(self,tool):
        # Two different causes, refused separately. This was one condition whose
        # single message -- 'Invalid tool registration' -- covered both, and it is
        # the very message `register_once`'s docstring below calls out as "saying
        # nothing about the real cause". Measured before the fix: a duplicate name,
        # an unknown risk level, and both at once each produced that one string.
        # The refusal is unchanged and still a ValueError; the reason is now the
        # reason. The risk is checked first because it is a property of the tool
        # being added, whereas a duplicate is a property of the registry's state.
        if tool.risk not in RISK_LEVELS:raise ValueError(f'Unknown tool risk level: {tool.risk}')
        if tool.name in self.items:raise ValueError(f'Tool already registered: {tool.name}')
        self.items[tool.name]=tool
    def get(self,name):
        if name not in self.items:raise LookupError('Unknown executable tool')
        return self.items[name]
    def describe(self,names=None):
        return [{'name':t.name,'risk':t.risk,'schema':t.schema,'runner':t.runner} for t in self.items.values() if names is None or t.name in names]


def register_once(registry, tools):
    """Register each tool unless its name is already present.

    Every ``register_*`` function in this runtime is **idempotent**, and that is a
    contract rather than a convenience: ``build_registry`` already calls them, so a
    caller that builds a registry and then registers explicitly is a normal pattern,
    and a second call must be a no-op rather than a duplicate-name error whose
    message says nothing about the cause. Twelve modules already skip an existing
    name inline; five did not, so re-registering raised -- and one module's test
    ASSERTED the raise, which made the deviation read as a deliberate choice to the
    next reader. This helper is the single statement of the convention, so the
    answer cannot differ by module.
    """
    for tool in tools:
        if tool.name not in registry.items:
            registry.add(tool)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('Provider redirect rejected')


def post_json(url,body,headers=None,timeout=PROVIDER_TIMEOUT_SECONDS):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('HTTPS provider configuration required')
    request=urllib.request.Request(url,data=encode(body).encode(),headers={'Content-Type':'application/json',**(headers or {})},method='POST')
    opener=urllib.request.build_opener(NoRedirect())
    with opener.open(request,timeout=timeout) as r:
        # Read one byte past the ceiling and refuse on the excess, rather than
        # reading the ceiling and trusting it: a body of exactly the limit must be
        # accepted, and a body one byte over must not be silently truncated into
        # valid-looking JSON.
        raw=r.read(MAX_PROVIDER_RESPONSE+1)
        if len(raw)>MAX_PROVIDER_RESPONSE:raise ValueError('Provider response too large')
        return json.loads(raw)


def pack_integrations_path(tenant):
    """<PACKS_DIR>/<tenant>/integrations.yaml -- the tenant's OWN config file.

    The name is matched against the pack charset BEFORE anything is joined, so
    '..' or a separator never reaches the filesystem, and the resolved path is
    then checked against the root the way app.packs.load_pack checks a pack, in
    case PACKS_DIR or a pack directory is a symlink pointing out of bounds.
    PACKS_DIR is read per call and never cached: it is environment, and
    environment changes between calls.
    """
    if not isinstance(tenant,str) or not TENANT_NAME.fullmatch(tenant):raise RuntimeError('Invalid tenant name')
    root=Path(os.environ.get('PACKS_DIR') or DEFAULT_PACKS_DIR).resolve()
    target=(root/tenant/PACK_INTEGRATIONS_FILE).resolve()
    if root not in target.parents:raise RuntimeError('Tenant configuration outside packs directory')
    return target


def read_pack_integrations(path):
    """Parse a pack-local integrations file, refusing every failure out loud.

    A file that EXISTS is authoritative: it is never skipped in favour of the
    operator JSON, because a silent fallback would let one typo in a tenant's
    own file be ignored while the tenant kept running on operator config. PyYAML
    is imported here rather than at module scope -- platform_runtime must stay
    importable, and its offline suite runnable, without it -- but a missing
    parser is a refusal, not a reason to serve the other source.
    """
    try:
        import yaml
    except ImportError:raise RuntimeError(f'Integration configuration unreadable: PyYAML required for {path}')
    try:
        data=yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,yaml.YAMLError) as exc:
        raise RuntimeError(f'Integration configuration unreadable: {path} ({exc})')
    if not isinstance(data,dict):raise RuntimeError(f'Integration configuration must be a mapping: {path}')
    return data


class IntegrationNotConfigured(RuntimeError):
    """No integration block exists for the tenant yet: a status, not a fault.

    A named class so callers (a channel status page) can tell "not configured"
    from an unreadable or malformed file without matching message text.
    """


def config(tenant):
    # Credentials are configured per tenant, never sent in pack or API output.
    # The tenant's own pack directory comes FIRST (tamoyil 1: yangi mijoz =
    # yangi YAML). Before this, every module resolved tenant configuration
    # through one operator-side JSON, so most of a non-retail tenant's config
    # lived outside its pack and onboarding was not YAML work. The JSON is the
    # unchanged fallback for tenants that have not moved -- same reads, same
    # messages, so no caller's error path shifted.
    local=pack_integrations_path(tenant)
    if local.is_file():return read_pack_integrations(local)
    path=os.environ.get('PLATFORM_INTEGRATIONS_FILE','')
    if not path:raise IntegrationNotConfigured('Integration configuration missing: set '
        'PLATFORM_INTEGRATIONS_FILE or add packs/<tenant>/'+PACK_INTEGRATIONS_FILE)
    with open(path,encoding='utf-8') as f:data=json.load(f)
    value=data.get(tenant)
    if not isinstance(value,dict):raise IntegrationNotConfigured('Tenant integration not configured: '
        'add it to PLATFORM_INTEGRATIONS_FILE or packs/<tenant>/'+PACK_INTEGRATIONS_FILE)
    return value


def secret(cfg,name):
    ref=cfg.get(name,'')
    if not isinstance(ref,str) or not CREDENTIAL_NAME.fullmatch(ref):raise RuntimeError('Invalid credential reference')
    value=os.environ.get(ref,'')
    if not value:raise RuntimeError('Missing provider credential')
    return value


def memory_put(e,t,a,p,key):
    ttl=p.get('ttl_seconds',0)
    with e.tx() as c:
        active=c.execute("SELECT 1 FROM p_steps WHERE id=? AND tenant=? AND status='running' AND lease>?",(key,t,e.clock())).fetchone()
        if not active:raise Conflict('Inactive step')
        c.execute('INSERT INTO p_memory VALUES(?,?,?,?,?) ON CONFLICT(tenant,agent,key) DO UPDATE SET value=excluded.value,expires=excluded.expires',(t,a,p['key'],p['value'],e.clock()+ttl if ttl else 0))
    return {'key':p['key'],'saved':True}


def memory_search(e,t,a,p,key):
    # Literal LIKE escaping; this is scoped keyword retrieval, NOT embedding RAG.
    q=p['query'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    with e.read() as c:
        rows=c.execute("SELECT key,value FROM p_memory WHERE tenant=? AND agent=? AND (expires=0 OR expires>?) AND value LIKE ? ESCAPE '\\' LIMIT ?",(t,a,e.clock(),'%'+q+'%',MEMORY_SEARCH_LIMIT)).fetchall()
    return {'matches':[dict(r) for r in rows]}


def record_create(e,t,a,p,key):
    with e.tx() as c:
        active=c.execute("SELECT 1 FROM p_steps WHERE id=? AND tenant=? AND status='running' AND lease>?",(key,t,e.clock())).fetchone()
        if not active:raise Conflict('Inactive step')
        c.execute('INSERT OR IGNORE INTO p_records VALUES(?,?,?,?,?)',(t,p['kind'],key,encode(p),e.clock()))
    return {'id':key,'kind':p['kind']}


def records_list(e,t,a,p,key):
    with e.read() as c:
        rows=c.execute('SELECT id,body,created FROM p_records WHERE tenant=? AND kind=? ORDER BY created DESC LIMIT ?',(t,p['kind'],RECORDS_LIST_LIMIT)).fetchall()
    return {'records':[{'id':r['id'],'body':json.loads(r['body']),'created':r['created']} for r in rows]}


def reports(e,t,a,p,key):
    with e.read() as c:
        rows=c.execute('SELECT status,count(*) count FROM p_tasks WHERE tenant=? GROUP BY status',(t,)).fetchall()
        records=c.execute('SELECT count(*) n FROM p_records WHERE tenant=?',(t,)).fetchone()['n']
    return {'tasks':{r['status']:r['count'] for r in rows},'records':records}


TELEGRAM_API = 'https://api.telegram.org'


def telegram_base_url(cfg):
    """The tenant's Bot API base: `telegram.base_url`, default Telegram's own.

    The rule is the model endpoint's, reused rather than restated
    (model_transport.validate_url): plain HTTPS, or numeric-loopback HTTP on an
    unprivileged port ONLY under the explicit `provider_mode: local_loopback`.
    It runs before the token is read, and the refusal names no URL, because the
    token becomes part of the path.
    """
    from .model_transport import validate_url
    base=cfg.get('base_url',TELEGRAM_API)
    if not isinstance(base,str):raise RuntimeError('Invalid Telegram base_url')
    base=base.rstrip('/')
    try:validate_url(cfg,base)
    except ValueError:
        raise RuntimeError('Telegram base_url must be plain HTTPS, or numeric loopback HTTP '
                           'with provider_mode: local_loopback') from None
    return base


def deliver_json(url,body,headers=None):
    """post_json for a customer-facing send, with the verdict the engine needs.

    A 4xx means the provider answered and did nothing -- blocked bot, unknown
    chat, bad token, a refused request under rate limit -- so it is raised as
    DeliveryRejected carrying only the status, which the engine records as
    ``failed``. A 5xx, a timeout or a reset says nothing about what the upstream
    did and stays a plain RuntimeError, which the engine records as ``uncertain``.
    Nothing raised here carries the URL: the Telegram URL carries the token.
    """
    from .engine import DeliveryRejected
    try:return post_json(url,body,headers)
    except urllib.error.HTTPError as error:
        if 400<=error.code<500:raise DeliveryRejected('http_%d'%error.code) from None
        raise RuntimeError('Provider request failed') from None


def telegram(e,t,a,p,key):
    from .engine import DeliveryRejected
    from .model_transport import LocalRequestRejected,local_mode,transport_for
    cfg=config(t)['telegram'];base=telegram_base_url(cfg);token=secret(cfg,'token_env')
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):raise RuntimeError('Invalid token configuration')
    url=base+'/bot'+token+'/sendMessage';message={'chat_id':p['conversation_id'],'text':p['text']}
    if local_mode(cfg):
        # The model's loopback transport: no proxy, no redirect, bounded bytes.
        # Its 4xx is the same definite rejection as the cloud path's.
        try:body=transport_for(cfg)(url,message,{})
        except LocalRequestRejected as error:
            if 400<=error.code<500:raise DeliveryRejected('http_%d'%error.code) from None
            raise RuntimeError('Local Telegram request failed') from None
        except (RuntimeError,ValueError):raise RuntimeError('Local Telegram request failed') from None
    else:body=deliver_json(url,message)
    # `ok: false` is the Bot API saying no (a gateway may return it with a 200).
    # Any other shape is not the Bot API's answer and stays uncertain.
    if body.get('ok') is False:raise DeliveryRejected('provider_ok_false')
    if body.get('ok') is not True:raise RuntimeError('Provider rejected delivery')
    return {'provider':'telegram','external_id':str(body['result']['message_id'])}


def instagram(e,t,a,p,key):
    cfg=config(t)['instagram'];version=cfg.get('graph_version','');account=cfg.get('account_id','')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+',version) or not str(account).isascii() or not str(account).isdigit():raise RuntimeError('Meta version/account must be configured')
    # Instagram Login API adapter; Facebook Login accounts need their own endpoint strategy.
    # One POST, one answer: a Graph API 4xx is a rejected request, mapped like Telegram's.
    body=deliver_json(f'https://graph.instagram.com/{version}/{account}/messages',{'recipient':{'id':p['conversation_id']},'message':{'text':p['text']}},{'Authorization':'Bearer '+secret(cfg,'token_env')})
    return {'provider':'instagram','external_id':str(body['message_id'])}


def sheets(e,t,a,p,key):
    cfg=config(t)['sheets'];sheet=cfg.get('spreadsheet_id','')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',sheet):raise RuntimeError('Invalid spreadsheet configuration')
    # RAW means untrusted text is not evaluated as a spreadsheet formula.
    target=urllib.parse.quote(cfg.get('range','Sheet1!A:D'),safe='')
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{sheet}/values/{target}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS'
    body=post_json(url,{'majorDimension':'ROWS','values':[[key]+p['values']]},{'Authorization':'Bearer '+secret(cfg,'token_env')})
    return {'provider':'sheets','updated_range':body['updates']['updatedRange'],'idempotency_reference':key}


def mcp_call(e,t,a,p,key):
    from .mcp import MCPClient
    cfg=config(t)['mcp']
    if p['name'] not in cfg.get('allowed_tools',[]):raise PermissionError('MCP tool not allowlisted')
    arguments=json.loads(p['arguments_json'])
    if not isinstance(arguments,dict):raise ValueError('MCP arguments must be object')
    schema=cfg.get('tool_schemas',{}).get(p['name'])
    if not isinstance(schema,dict) or schema.get('type')!='object':
        raise PermissionError('Pinned MCP argument schema required')
    validate_schema(arguments,schema)
    return MCPClient(cfg['url'],secret(cfg,'token_env')).call(p['name'],arguments)


def build_registry(catalog=None, shop=None):
    r=Registry()
    from .google_adapters import register_tools
    register_tools(r)
    from .crm.crm_gateway import register_crm_tools
    register_crm_tools(r)

    from .knowledge import tool_search
    r.add(Tool('knowledge.search','read',obj({'collection':string(128),'query':string(500),
        'limit':{'type':'integer','minimum':1,'maximum':5}},['collection','query']),tool_search))
    from .connectors import tool_read
    from .speech import tool_tts
    from .database.gateway import tool_read as db_read, tool_plan, tool_write, tool_catalog
    db_args = {'connection': string(128), 'request_json': string(12000)}
    r.add(Tool('database.catalog', 'read', obj({}), tool_catalog))
    r.add(Tool('database.read', 'read', obj(db_args), db_read, external=True))
    r.add(Tool('database.plan_write', 'read', obj(db_args), tool_plan))
    r.add(Tool('database.write', 'write', obj({**db_args, 'plan_fingerprint': string(64)}), tool_write, external=True))
    r.add(Tool('connectors.read','read',obj({
        'connection':string(128),'table':string(63),
        'columns':{'type':'array','minItems':1,'maxItems':40,'items':string(63)},
        'limit':{'type':'integer','minimum':1,'maximum':100},
        'where':obj({'column':string(63),'equals':string(1000)})
    },['connection','table','columns']),tool_read,external=True))
    from .sheets import register_sheets_tools
    register_sheets_tools(r)
    from .business_graph import register_graph_tools
    register_graph_tools(r)
    # The inventory block reads through the graph, so it registers after it and
    # declares no transport, no connection and no register of its own.
    from .inventory import register_inventory_tools
    register_inventory_tools(r)
    from .oversight import register_oversight_tools
    register_oversight_tools(r)
    from .workforce import register_workforce_tools
    register_workforce_tools(r)
    from .supervisor import register_supervisor_tools
    register_supervisor_tools(r)
    from .assets import register_asset_tools
    register_asset_tools(r)
    from .vision import register_vision_tools
    register_vision_tools(r)
    from .manufacturing import register_manufacturing_tools
    register_manufacturing_tools(r)
    from .oee import register_oee_tools
    register_oee_tools(r)
    from .telephony import register_telephony_tools
    register_telephony_tools(r)
    from .whatsapp import register_whatsapp_tools
    register_whatsapp_tools(r)
    from .whatsapp_inbound import register_whatsapp_inbound_tools
    register_whatsapp_inbound_tools(r)
    from .documents import register_document_tools
    register_document_tools(r)
    from .erp import register_erp_tools
    register_erp_tools(r)
    from .escalation import register_escalation_tools
    register_escalation_tools(r)
    r.add(Tool('voice.tts','write',obj({'text':string(1000),
        'mood':{'type':'string','enum':['Neutral','Cheerful','Happy','Sad']}},['text']),tool_tts,external=True))
    r.add(Tool('memory.put','write',obj({'key':string(128),'value':string(8000),'ttl_seconds':{'type':'integer','minimum':0,'maximum':31536000}},['key','value']),memory_put))
    r.add(Tool('memory.search','read',obj({'query':string(500)}),memory_search))
    r.add(Tool('records.create','write',obj({'kind':string(64),'title':string(200),'body':string(8000)}),record_create))
    r.add(Tool('records.list','read',obj({'kind':string(64)}),records_list))
    r.add(Tool('reports.summary','read',obj({}),reports))
    r.add(Tool('telegram.send','write',obj({'conversation_id':string(128),'text':string(4096)}),telegram,external=True))
    r.add(Tool('instagram.send','write',obj({'conversation_id':string(128),'text':string(1000)}),instagram,external=True))
    r.add(Tool('sheets.append','write',obj({'values':{'type':'array','maxItems':40,'items':string(4000)}}),sheets,external=True))
    r.add(Tool('mcp.call','write',obj({'name':string(128),'arguments_json':string(12000)}),mcp_call,external=True))
    if catalog:
        def products(e,t,a,p,key):return {'products':catalog(t,p['query'])}
        r.add(Tool('products.search','read',obj({'query':string(500)}),products))
    if shop:
        # shop.info / orders.draft read the tenant's shop through the injected reader.
        from .shop_tools import register_shop_tools
        register_shop_tools(r,shop)
    # Only implemented, non-simulated runner operations are enabled.
    r.add(Tool('fs.list','read',obj({'dir':string(1000)}),runner=True))
    r.add(Tool('fs.read_text','read',obj({'file':string(1000)}),runner=True))
    return r


@functools.lru_cache(maxsize=1)
def known_tool_names():
    """Every name build_registry can register, catalog-conditional ones included.

    The pack contract is checked against this, not against one live registry, so
    a tenant without a catalog may still declare products.search in its YAML.
    """
    return frozenset(build_registry(lambda tenant, query: [], lambda tenant: {}).items)


def unknown_tools(names):
    """Sorted tool names a pack declares that no runtime adapter provides.

    Returned rather than raised so the caller can name every offending tool in
    one message instead of failing on the first.
    """
    known = known_tool_names()
    return sorted({name for name in names if name not in known})


def tenant_for_instagram_account(account):
    path=os.environ.get('PLATFORM_INTEGRATIONS_FILE','')
    if not path:raise RuntimeError('Integration mapping missing')
    with open(path,encoding='utf-8') as f:data=json.load(f)
    matches=[tenant for tenant,cfg in data.items() if str(cfg.get('instagram',{}).get('account_id',''))==str(account)]
    if not account or len(matches)!=1:raise RuntimeError('Ambiguous or unknown Meta account')
    return matches[0]
