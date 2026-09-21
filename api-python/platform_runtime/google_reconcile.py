"""Owner-triggered, read-only Google write reconciliation.

Only positive, fully matched provider evidence settles an uncertain step. Missing
objects, partial/mismatched data, multiple Gmail matches and transport failures
never prove non-delivery. No send/create request or automatic retry is performed.
The journal and engine step settle in one transaction, with a second authority,
configuration, session, credential-generation and uncertain-state check.
"""
import base64
import json
import urllib.parse
from email import policy
from email.parser import BytesParser
from .engine import Conflict, Forbidden, NotFound, encode, digest
from .oauth import OAuthError, bounded
from .google_adapters import SCOPES, WRITES, authorize_args, api_transport, component, GoogleHTTPError, _date
from .google_sync import objects, GMAIL


class GoogleReconciler:
    def __init__(self,engine,manager,resources,transport=api_transport):
        self.e,self.m,self.resources,self.transport=engine,manager,resources,transport

    def _load(self,db,tenant,step,actor,session_guard=None):
        self.e.require_active(db,tenant)
        self.e.require_authority(db,tenant,'web',actor,('owner',))
        if session_guard is not None: session_guard(db)
        row=db.execute('SELECT s.*,t.agent FROM p_steps s JOIN p_tasks t ON t.tenant=s.tenant AND t.id=s.task WHERE s.tenant=? AND s.id=?',(tenant,step)).fetchone()
        if not row: raise NotFound('Step not found')
        if row['tool'] not in WRITES or row['status']!='uncertain': raise Conflict('Uncertain Google write required')
        dispatch=db.execute('SELECT * FROM p_google_dispatch WHERE tenant=? AND step=?',(tenant,step)).fetchone()
        if not dispatch or dispatch['status'] not in {'uncertain','dispatching','succeeded'}:
            raise Conflict('Recoverable dispatch evidence required')
        args=json.loads(row['args']);connection=args['connection']
        if dispatch['connection']!=connection: raise Forbidden('Dispatch connection mismatch')
        method,url,body,reference=authorize_args(row['tool'],args,self.resources,step)
        required=[SCOPES[row['tool']]]
        if row['tool']=='google.gmail.send':required.append(SCOPES['google.gmail.read'])
        credential=self.m._agent(db,tenant,connection,row['agent'],required)
        if credential['generation']!=dispatch['generation']:raise Forbidden('Original dispatch credential generation required')
        # Same policy and configuration snapshot as the original approved step.
        canonical=self.e._validated(tenant,row['agent'],[{'tool':row['tool'],'args':args}])[0]
        if canonical[4]!=row['fingerprint']:raise Forbidden('Original step policy changed')
        expected=digest({'url':url,'body':body,'step_fingerprint':row['fingerprint'],'connection':connection,
                         'generation':dispatch['generation'],'config':self.m.config_hash,'resources':digest(self.resources)})
        if expected!=dispatch['fingerprint']:raise Forbidden('Dispatch fingerprint mismatch')
        return row,dispatch,args,reference,required

    def reconcile(self,tenant,step,actor,*,session_guard=None):
        with self.e.read() as db:
            row,dispatch,args,reference,required=self._load(db,tenant,step,actor,session_guard)
        connection=args['connection']
        grant=self.m.access(tenant,connection,row['agent'],required)
        if grant.generation!=dispatch['generation']:raise Forbidden('Credential changed')
        def get(url):
            self.m.fence(tenant,connection,row['agent'],required,grant)
            with self.e.read() as db:self._load(db,tenant,step,actor,session_guard)
            response=self.transport('GET',url,grant.token,None)
            if not isinstance(response,dict) or len(encode(response).encode())>1_000_000:
                raise OAuthError('Invalid bounded provider evidence')
            self.m.fence(tenant,connection,row['agent'],required,grant)
            return response
        try:
            if row['tool']=='google.calendar.create':
                external_id=self._calendar(args,reference,step,get)
            else:external_id=self._gmail(args,reference,get)
        except GoogleHTTPError as error:
            if error.status in {404,410}:external_id=None
            else:raise
        # Even a negative result is not returned to a revoked session.
        with self.e.tx() as db:
            current,journal,_,_,_=self._load(db,tenant,step,actor,session_guard)
            credential=self.m._agent(db,tenant,connection,row['agent'],required)
            if credential['generation']!=grant.generation or credential['status']!='active' or credential['expires']<=self.e.clock():
                raise Forbidden('Reconciliation credential fenced')
            if not external_id:
                self.e.audit(db,tenant,row['task'],'google.reconcile_unresolved',actor,{'step':step})
                return {'status':'uncertain','reason':'No unique matching positive provider evidence','retried_write':False}
            receipt={'provider':'google','operation':row['tool'],'external_id':external_id,**reference,
                     'reconciled':True,'evidence_kind':'provider_readback'}
            db.execute("UPDATE p_google_dispatch SET status='succeeded',receipt=?,updated=? WHERE tenant=? AND step=?",
                       (encode(receipt),self.e.clock(),tenant,step))
            db.execute("UPDATE p_steps SET status='succeeded',result=?,error='provider_reconciled',claim='',lease=0 WHERE tenant=? AND id=? AND status='uncertain'",
                       (encode(receipt),tenant,step))
            self.e.audit(db,tenant,row['task'],'google.provider_reconciled',actor,{'step':step,'external_id':external_id})
            self.e._refresh(db,tenant,row['task'])
            return {'status':'succeeded','receipt':receipt,'retried_write':False}

    def _calendar(self,args,reference,step,get):
        response=get('https://www.googleapis.com/calendar/v3/calendars/'+component(args['calendar'])+'/events/'+component(reference['event_id']))
        if response.get('id')!=reference['event_id'] or response.get('status')=='cancelled':return None
        extended=response.get('extendedProperties',{})
        if not isinstance(extended,dict):return None
        private=extended.get('private',{})
        if not isinstance(private,dict) or private.get('platform_step')!=digest({'step':step}):return None
        if response.get('attendees'):return None
        if response.get('summary')!=args['summary'] or response.get('description','')!=args.get('description',''):return None
        try:
            if _date(response['start']['dateTime'])!=_date(args['start']) or _date(response['end']['dateTime'])!=_date(args['end']):return None
        except (KeyError,TypeError,ValueError):return None
        return reference['event_id']

    def _gmail(self,args,reference,get):
        message_id='<ap.'+reference['message_reference']+'@agent-platform.invalid>'
        query=urllib.parse.urlencode({'q':'in:sent rfc822msgid:'+message_id,'maxResults':2,'includeSpamTrash':'true'})
        response=get(GMAIL+'messages?'+query)
        matches=objects(response,'messages',2)
        if len(matches)!=1 or response.get('nextPageToken'):return None
        mid=bounded(matches[0].get('id'),256)
        response=get(GMAIL+'messages/'+component(mid)+'?format=raw')
        labels=response.get('labelIds',[])
        if not isinstance(labels,list) or len(labels)>100 or response.get('id')!=mid or 'SENT' not in labels:return None
        raw=response.get('raw')
        if not isinstance(raw,str) or len(raw)>100000:return None
        try:
            binary=base64.b64decode(raw+'='*((-len(raw))%4),altchars=b'-_',validate=True)
            msg=BytesParser(policy=policy.default).parsebytes(binary)
            if msg.defects or msg.is_multipart() or msg.get_content_type()!='text/plain':return None
            for name,expected in [('Message-ID',message_id),('To',args['to']),('Subject',args['subject'])]:
                values=msg.get_all(name,[])
                if len(values)!=1 or str(values[0])!=expected:return None
            if msg.get_all('Cc',[]) or msg.get_all('Bcc',[]):return None
            # EmailMessage.set_content appends a terminal newline; reconstruct the
            # expected MIME body rather than trimming arbitrary whitespace.
            _,_,body,_=authorize_args('google.gmail.send',args,self.resources,'unused')
            expected_msg=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(body['raw']))
            normalize=lambda value:value.replace('\r\n','\n').replace('\r','\n')
            if normalize(msg.get_content())!=normalize(expected_msg.get_content()):return None
        except (ValueError,TypeError,LookupError):return None
        return mid
