"""Bounded Google metadata snapshots and incremental sync, with durable cursors.

Record versions are LOCAL serialized observation sequences, not provider versions.
Calendar sequence/updated, Drive version and Gmail historyId are not interchangeable.
Each page and cursor commit atomically under stream lease and OAuth generation.
No message bodies, file bytes, automatic scheduling or automatic destructive reset.
"""
import urllib.parse
from .engine import Conflict, Forbidden, digest, encode
from .oauth import OAuthError, bounded, ident
from .google_adapters import SCOPES, api_transport, component, GoogleHTTPError
from .google_oauth import strict_json
from .sync_store import SyncStore

READ_TOOL = {'gmail':'google.gmail.list', 'drive':'google.drive.list', 'calendar':'google.calendar.list'}
GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me/'
DRIVE = 'https://www.googleapis.com/drive/v3/'
FILE_FIELDS = 'id,name,mimeType,modifiedTime,version,trashed'


class ResetRequired(Conflict):
    pass


def stream_id(kind, calendar=''):
    if kind not in READ_TOOL: raise ValueError('Unsupported sync kind')
    if kind == 'calendar': bounded(calendar,256)
    elif calendar: raise ValueError('Calendar is only valid for calendar sync')
    return 'google_'+kind+'_'+digest({'calendar':calendar})[:24]


def token(value):
    return bounded(value,1500)


def objects(body,key,maximum=100):
    result=body.get(key,[])
    if not isinstance(result,list) or len(result)>maximum or any(not isinstance(x,dict) for x in result):
        raise OAuthError('Invalid or oversized provider collection')
    return result


def text_fields(item,fields):
    out={}
    for field in fields:
        if field in item:
            value=item[field]
            if not isinstance(value,str) or len(value)>4096 or '\x00' in value: raise OAuthError('Invalid metadata text')
            out[field]=value
    return out


class GoogleSync:
    def __init__(self,engine,manager,resources,transport=api_transport):
        self.e,self.m,self.resources,self.transport=engine,manager,resources,transport
        self.store=SyncStore(engine)

    def _guard(self,db,tenant,connection,agent,actor,kind,calendar,grant=None,session_guard=None):
        self.store._authority(db,tenant,actor)
        policy=self.e.policy(tenant,agent)
        if READ_TOOL[kind] not in policy.get('tools',[]): raise Forbidden('Agent sync tool denied')
        if kind=='calendar' and calendar not in self.resources['calendar_ids']:
            raise Forbidden('Calendar not allowlisted')
        row=self.m._agent(db,tenant,connection,agent,[SCOPES[READ_TOOL[kind]]])
        if row['status'] not in {'active','refreshing'}: raise Forbidden('Active sync connection required')
        if grant and (row['status']!='active' or row['generation']!=grant.generation or row['expires']<=self.e.clock()):
            raise Forbidden('Sync credential fenced')
        if session_guard is not None: session_guard(db)
        return row

    def _cursor(self,raw,kind,calendar):
        if not raw: return {'v':1,'kind':kind,'calendar':calendar,'phase':'start','anchor':'','page':''}
        data=strict_json(raw.encode())
        if set(data)!={'v','kind','calendar','phase','anchor','page'} or type(data['v']) is not int or data['v']!=1:
            raise Conflict('Unsupported sync checkpoint')
        if data['kind']!=kind or data['calendar']!=calendar or data['phase'] not in {'snapshot','delta'}:
            raise Conflict('Sync checkpoint binding mismatch')
        for key in ('anchor','page'):
            if data[key]: token(data[key])
            elif data[key]!='': raise Conflict('Invalid sync token')
        if kind!='calendar' or data['phase']=='delta': token(data['anchor'])
        return data

    def page(self,tenant,connection,agent,actor,kind,calendar='',*,session_guard=None):
        for value in (tenant,connection,agent): ident(value)
        stream=stream_id(kind,calendar)
        with self.e.read() as db:
            self._guard(db,tenant,connection,agent,actor,kind,calendar,session_guard=session_guard)
        lease=self.store.claim(tenant,connection,stream,actor,300)
        try:
            grant=self.m.access(tenant,connection,agent,[SCOPES[READ_TOOL[kind]]])
            def guard(db): self._guard(db,tenant,connection,agent,actor,kind,calendar,grant,session_guard)
            def get(url):
                with self.e.read() as db: guard(db)
                self.store.renew(tenant,connection,stream,actor,lease['claim'],300)
                response=self.transport('GET',url,grant.token,None)
                if not isinstance(response,dict) or len(encode(response).encode())>1_000_000:
                    raise OAuthError('Invalid bounded Google response')
                with self.e.read() as db: guard(db)
                return response
            state=self._cursor(lease['cursor'],kind,calendar)
            next_state,items,complete=self._fetch(state,get)
            if state['page'] and next_state['page']==state['page']:
                raise Conflict('Provider repeated pagination token')
            unique={}
            for item in items: unique[item['id']]=item
            if len(unique)>100: raise OAuthError('Too many changed records; cursor not advanced')
            events=[{**item,'version':(lease['revision']+1)*1000+index} for index,item in enumerate(unique.values())]
            receipt=self.store.commit(tenant,connection,stream,actor,lease['claim'],
                digest({'revision':lease['revision'],'cursor':lease['cursor']}),lease['cursor'],encode(next_state),events,guard=guard)
            return {'stream':stream,'revision':receipt['revision'],'changed_records':receipt['changed_records'],
                    'phase':next_state['phase'],'caught_up':complete,'untrusted_content':True,
                    'coverage':'metadata_only','version_kind':'local_observation_sequence'}
        finally:
            try: self.store.release(tenant,connection,stream,actor,lease['claim'])
            except (Conflict,Forbidden): pass

    def _fetch(self,state,get):
        kind,phase=state['kind'],state['phase']
        if phase=='start' and kind in {'gmail','drive'}:
            url=GMAIL+'profile' if kind=='gmail' else DRIVE+'changes/startPageToken'
            key='historyId' if kind=='gmail' else 'startPageToken'
            return {**state,'phase':'snapshot','anchor':token(get(url).get(key))},[],False
        if kind=='calendar': return self._calendar(state,get)
        if phase=='snapshot': return self._snapshot(state,get)
        try: return self._gmail_history(state,get) if kind=='gmail' else self._drive_changes(state,get)
        except GoogleHTTPError as error:
            if error.status==(404 if kind=='gmail' else 410):
                raise ResetRequired('Google cursor expired; explicit owner reset required') from None
            raise

    def _message(self,mid,get):
        mid=bounded(mid,256)
        try: obj=get(GMAIL+'messages/'+component(mid)+'?format=minimal')
        except GoogleHTTPError as error:
            if error.status==404: return {'id':mid,'deleted':True,'data':{}}
            raise
        if obj.get('id')!=mid: raise OAuthError('Message identity mismatch')
        data=text_fields(obj,('id','threadId','historyId'))
        labels=obj.get('labelIds',[])
        if not isinstance(labels,list) or len(labels)>100: raise OAuthError('Invalid label list')
        data['labelIds']=sorted({bounded(v,256) for v in labels})
        return {'id':mid,'deleted':False,'data':data}

    def _file(self,obj,fid=None,removed=False):
        mid=bounded(fid if fid is not None else obj.get('id'),256)
        if removed: return {'id':mid,'deleted':True,'data':{}}
        if obj.get('id')!=mid: raise OAuthError('File identity mismatch')
        data=text_fields(obj,('id','name','mimeType','modifiedTime','version'))
        if type(obj.get('trashed',False)) is not bool: raise OAuthError('Invalid file trash status')
        return {'id':mid,'deleted':obj.get('trashed',False),'data':{} if obj.get('trashed') else data}

    def _snapshot(self,state,get):
        params={'maxResults':20,'includeSpamTrash':'true'} if state['kind']=='gmail' else {
            'pageSize':100,'fields':'nextPageToken,incompleteSearch,files('+FILE_FIELDS+')','spaces':'drive','corpora':'user'}
        if state['page']: params['pageToken']=state['page']
        response=get((GMAIL+'messages' if state['kind']=='gmail' else DRIVE+'files')+'?'+urllib.parse.urlencode(params))
        if response.get('incompleteSearch'): raise OAuthError('Drive search incomplete; cursor not advanced')
        items=([self._message(x.get('id'),get) for x in objects(response,'messages',20)] if state['kind']=='gmail'
               else [self._file(x) for x in objects(response,'files')])
        page=token(response['nextPageToken']) if 'nextPageToken' in response else ''
        return {**state,'page':page,'phase':'snapshot' if page else 'delta'},items,False

    def _gmail_history(self,state,get):
        params={'startHistoryId':state['anchor'],'maxResults':10}
        if state['page']: params['pageToken']=state['page']
        response=get(GMAIL+'history?'+urllib.parse.urlencode(params))
        changed={}
        for history in objects(response,'history',10):
            for field in ('messagesAdded','labelsAdded','labelsRemoved','messagesDeleted'):
                for change in objects(history,field,100):
                    msg=change.get('message')
                    if not isinstance(msg,dict): raise OAuthError('History message required')
                    changed[bounded(msg.get('id'),256)]=field=='messagesDeleted'
        if len(changed)>100: raise OAuthError('Too many history messages; cursor not advanced')
        items=[{'id':mid,'deleted':True,'data':{}} if deleted else self._message(mid,get) for mid,deleted in changed.items()]
        page=token(response['nextPageToken']) if 'nextPageToken' in response else ''
        anchor=state['anchor'] if page else token(response.get('historyId'))
        return {**state,'page':page,'anchor':anchor},items,not page

    def _drive_changes(self,state,get):
        params={'pageToken':state['page'] or state['anchor'],'pageSize':100,'spaces':'drive','includeRemoved':'true',
                'fields':'nextPageToken,newStartPageToken,changes(fileId,removed,file('+FILE_FIELDS+'))'}
        response=get(DRIVE+'changes?'+urllib.parse.urlencode(params))
        items=[]
        for change in objects(response,'changes'):
            removed=change.get('removed',False)
            if type(removed) is not bool: raise OAuthError('Invalid removed flag')
            file=change.get('file',{})
            if not isinstance(file,dict): raise OAuthError('Invalid file object')
            items.append(self._file(file,change.get('fileId'),removed))
        page=token(response['nextPageToken']) if 'nextPageToken' in response else ''
        anchor=state['anchor'] if page else token(response.get('newStartPageToken'))
        return {**state,'page':page,'anchor':anchor},items,not page

    def _calendar(self,state,get):
        params={'maxResults':100,'singleEvents':'false','showDeleted':'true'}
        if state['phase']=='delta': params['syncToken']=state['anchor']
        if state['page']: params['pageToken']=state['page']
        try: response=get('https://www.googleapis.com/calendar/v3/calendars/'+component(state['calendar'])+'/events?'+urllib.parse.urlencode(params))
        except GoogleHTTPError as error:
            if error.status==410: raise ResetRequired('Calendar cursor expired; explicit owner reset required') from None
            raise
        items=[]
        for item in objects(response,'items'):
            mid=bounded(item.get('id'),256);deleted=item.get('status')=='cancelled'
            data={} if deleted else text_fields(item,('id','summary','description','status','updated','etag','recurringEventId'))
            if not deleted:
                for key in ('start','end','originalStartTime'):
                    if key in item:
                        if not isinstance(item[key],dict): raise OAuthError('Calendar date object required')
                        data[key]=text_fields(item[key],('date','dateTime','timeZone'))
                if 'recurrence' in item:
                    rules=item['recurrence']
                    if not isinstance(rules,list) or len(rules)>30: raise OAuthError('Invalid recurrence')
                    data['recurrence']=[bounded(x,1000) for x in rules]
            items.append({'id':mid,'deleted':deleted,'data':data})
        page=token(response['nextPageToken']) if 'nextPageToken' in response else ''
        anchor=state['anchor'] if page else token(response.get('nextSyncToken'))
        return {**state,'phase':('delta' if not page or state['phase']=='delta' else 'snapshot'),'page':page,'anchor':anchor},items,not page
