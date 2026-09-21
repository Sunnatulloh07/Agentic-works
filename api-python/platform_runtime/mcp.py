"""Bounded MCP Streamable HTTP client with JSON and finite SSE responses.

No stdio, OAuth flow or indefinitely streaming tool supported. Endpoint and credentials
are trusted deployment config; model cannot choose them. Every MCP call is write-risk.
"""
import json
import urllib.request
import urllib.parse
from .tools import NoRedirect

# An MCP server is a deployment-trusted endpoint but an untrusted peer: it chooses the
# body, the framing and the protocol version it answers with. The byte ceiling and the
# timeout were literals, and the accepted-version set was an inline literal twice.
MAX_RESPONSE_BYTES = 1_000_000
TRANSPORT_TIMEOUT_SECONDS = 25
INITIAL_PROTOCOL_VERSION = '2025-03-26'
SUPPORTED_PROTOCOL_VERSIONS = frozenset({'2025-03-26', '2025-06-18'})


class MCPClient:
    def __init__(self,url,token,transport=None):
        p=urllib.parse.urlparse(url)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:raise ValueError('HTTPS required')
        self.url=url;self.token=token;self.session='';self.protocol=INITIAL_PROTOCOL_VERSION
        self.transport=transport or self._http

    def _http(self,body,headers):
        request=urllib.request.Request(self.url,data=json.dumps(body).encode(),headers=headers,method='POST')
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=TRANSPORT_TIMEOUT_SECONDS) as response:
            session=response.headers.get('Mcp-Session-Id','')
            if session:self.session=session
            if response.status==202:return None
            raw=response.read(MAX_RESPONSE_BYTES+1)
            if len(raw)>MAX_RESPONSE_BYTES:raise ValueError('MCP response too large')
            if not raw:return None
            if 'text/event-stream' in response.headers.get('Content-Type',''):
                for block in raw.decode().replace('\r\n','\n').split('\n\n'):
                    data='\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
                    if data:
                        value=json.loads(data)
                        if value.get('id')==body.get('id'):return value
                raise ValueError('MCP response missing request ID')
            return json.loads(raw)

    def rpc(self,method,params,request_id=None):
        body={'jsonrpc':'2.0','method':method,'params':params}
        if request_id is not None:body['id']=request_id
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream',
                 'Authorization':'Bearer '+self.token,'MCP-Protocol-Version':self.protocol}
        if self.session:headers['Mcp-Session-Id']=self.session
        response=self.transport(body,headers)
        if request_id is None:return None
        if not isinstance(response,dict) or response.get('id')!=request_id or response.get('jsonrpc')!='2.0' or 'error' in response:
            raise RuntimeError('MCP protocol error')
        return response['result']

    def call(self,name,arguments):
        hello=self.rpc('initialize',{'protocolVersion':self.protocol,'capabilities':{},
                       'clientInfo':{'name':'agent-platform','version':'0.1.0'}},1)
        version=hello.get('protocolVersion')
        if version not in SUPPORTED_PROTOCOL_VERSIONS:raise RuntimeError('Unsupported MCP protocol version')
        self.protocol=version
        self.rpc('notifications/initialized',{})
        result=self.rpc('tools/call',{'name':name,'arguments':arguments},2)
        if result.get('isError'):raise RuntimeError('MCP tool failed')
        return result
