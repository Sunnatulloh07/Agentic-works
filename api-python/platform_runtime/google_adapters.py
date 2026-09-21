"""Typed, bounded Google tools with pinned hosts and per-step dispatch journal.

No blind write retry. A timeout is uncertain, never reported as delivered.
Provider content is untrusted input and must not become system instructions.
"""
import base64
import datetime
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.policy import SMTP
from .engine import Conflict, Forbidden, encode, digest
from .oauth import OAuthError, bounded
from .google_oauth import configured_manager, strict_json
from .tools import Tool, obj, string, config, NoRedirect, register_once

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p_google_dispatch(
 tenant TEXT NOT NULL, step TEXT NOT NULL, connection TEXT NOT NULL,
 generation INTEGER NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 receipt TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(tenant,step));
'''

PREFIX = 'https://www.googleapis.com/auth/'
SCOPES = {
    'google.gmail.list': PREFIX+'gmail.readonly',
    'google.gmail.read': PREFIX+'gmail.readonly',
    'google.gmail.send': PREFIX+'gmail.send',
    'google.drive.list': PREFIX+'drive.metadata.readonly',
    'google.drive.metadata': PREFIX+'drive.metadata.readonly',
    'google.calendar.list': PREFIX+'calendar.events.readonly',
    'google.calendar.create': PREFIX+'calendar.events',
}
WRITES = {'google.gmail.send', 'google.calendar.create'}


def component(value):
    bounded(value, 256)
    return urllib.parse.quote(value, safe='')


def configured_resources(tenant, connection):
    raw = config(tenant).get('google_resources', {}).get(connection)
    if not isinstance(raw, dict) or set(raw) != {'recipient_emails', 'calendar_ids'}:
        raise Forbidden('Explicit Google resource policy required')
    for key in raw:
        if not isinstance(raw[key], list) or len(raw[key]) > 100 or any(not isinstance(x, str) for x in raw[key]):
            raise ValueError('Invalid resource allowlist')
        for item in raw[key]: bounded(item, 256)
    return raw


def _mailbox(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', value):
        raise ValueError('Single ASCII email address required')
    return value


def _date(value):
    bounded(value, 64)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None: raise ValueError()
    except ValueError:
        raise ValueError('Timezone-qualified ISO timestamp required') from None
    return parsed


def authorize_args(name, args, resources, key):
    if name == 'google.gmail.send':
        recipient = _mailbox(args['to'])
        if recipient not in resources['recipient_emails']: raise Forbidden('Email recipient not allowlisted')
        if any(c in args['subject'] for c in '\r\n\x00'): raise ValueError('Invalid subject')
        message = EmailMessage(policy=SMTP)
        message['To'] = recipient; message['Subject'] = args['subject']
        # Correlation identifier only. Gmail send does not promise idempotency.
        reference = digest({'step': key})
        message['Message-ID'] = '<ap.'+reference+'@agent-platform.invalid>'
        message.set_content(args['text'])
        body = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')}
        return 'POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send', body, {'message_reference': reference}
    if name == 'google.gmail.list':
        query = urllib.parse.urlencode({'maxResults': args.get('limit', 20), 'q': args.get('query', ''),
                                       **({'pageToken': args['page_token']} if args.get('page_token') else {})})
        return 'GET', 'https://gmail.googleapis.com/gmail/v1/users/me/messages?'+query, None, {}
    if name == 'google.gmail.read':
        return 'GET', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/'+component(args['id'])+'?format=full', None, {}
    if name == 'google.drive.list':
        params = {'pageSize': args.get('limit', 20), 'fields': 'nextPageToken,files(id,name,mimeType,modifiedTime,version)', 'q': 'trashed = false'}
        if args.get('page_token'): params['pageToken'] = args['page_token']
        return 'GET', 'https://www.googleapis.com/drive/v3/files?'+urllib.parse.urlencode(params), None, {}
    if name == 'google.drive.metadata':
        return 'GET', 'https://www.googleapis.com/drive/v3/files/'+component(args['id'])+'?fields=id,name,mimeType,modifiedTime,version', None, {}
    if name.startswith('google.calendar.'):
        if args['calendar'] not in resources['calendar_ids']: raise Forbidden('Calendar not allowlisted')
        base = 'https://www.googleapis.com/calendar/v3/calendars/'+component(args['calendar'])+'/events'
        if name == 'google.calendar.list':
            params = {'maxResults': args.get('limit', 20), 'singleEvents': 'true'}
            if args.get('page_token'): params['pageToken'] = args['page_token']
            if args.get('time_min'): _date(args['time_min']); params['timeMin'] = args['time_min']
            return 'GET', base+'?'+urllib.parse.urlencode(params), None, {}
        if name == 'google.calendar.create':
            if _date(args['end']) <= _date(args['start']): raise ValueError('Event end must follow start')
            # Google event IDs accept base32hex, hex is a valid subset.
            event_id = 'ap'+digest({'step': key})
            body = {'id': event_id, 'summary': args['summary'],
                    'start': {'dateTime': args['start']}, 'end': {'dateTime': args['end']},
                    'extendedProperties': {'private': {'platform_step': digest({'step': key})}}}
            if 'description' in args: body['description'] = args['description']
            return 'POST', base+'?sendUpdates=none', body, {'event_id': event_id}
    raise ValueError('Unknown Google operation')


class GoogleHTTPError(OAuthError):
    """Sanitized status only; no provider body or credential headers."""
    def __init__(self, status):
        self.status = status
        super().__init__('Google API HTTP ' + str(status))


def api_transport(method, url, token, body=None):
    parsed = urllib.parse.urlsplit(url)
    host_path = (
        parsed.hostname == 'gmail.googleapis.com' and
        (parsed.path in {'/gmail/v1/users/me/history', '/gmail/v1/users/me/profile', '/gmail/v1/users/me/messages'}
         or parsed.path.startswith('/gmail/v1/users/me/messages/'))
    ) or (
        parsed.hostname == 'www.googleapis.com' and
        (parsed.path in {'/drive/v3/files', '/drive/v3/changes', '/drive/v3/changes/startPageToken'}
         or parsed.path.startswith(('/drive/v3/files/', '/calendar/v3/calendars/')))
    )
    if (parsed.scheme != 'https'  or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None,443}
            or parsed.hostname not in {'gmail.googleapis.com', 'www.googleapis.com'}
            or not host_path
            or method not in {'GET', 'POST'}):
        raise OAuthError('Unregistered Google API destination')
    bounded(token, 16000)
    if not token.isascii() or any(c.isspace() for c in token): raise OAuthError('Invalid credential')
    data = None if body is None else encode(body).encode('utf-8')
    if data and len(data) > 64000: raise ValueError('Google request exceeds limit')
    request = urllib.request.Request(url, data, {'Authorization': 'Bearer '+token,
                    'Accept': 'application/json', 'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            if response.status not in {200,201}: raise OAuthError('Google response rejected')
            return strict_json(response.read(1_000_001))
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise GoogleHTTPError(status) from None
    except Exception:
        raise OAuthError('Google API request failed; no automatic retry') from None


def validate_step(engine, tenant, agent, name, args):
    connection = args['connection']
    if connection not in engine.policy(tenant,agent).get('allowed_connections', []):
        raise Forbidden('Agent connection access denied')
    resources = configured_resources(tenant, connection)
    authorize_args(name, args, resources, 'validation')
    manager = configured_manager(engine, tenant, connection)
    with engine.read() as db:
        row = manager._agent(db, tenant, connection, agent, [SCOPES[name]])
        if row['status'] not in {'active', 'refreshing'}: raise Forbidden('Google connection is not authorized')
        return {'config': manager.config_hash, 'resources': digest(resources), 'generation': row['generation'], 'account': row['account']}


class GoogleAdapter:
    def __init__(self, engine, manager, resources, transport=api_transport):
        self.e, self.m, self.resources, self.transport = engine, manager, resources, transport

    def _active_step(self, tenant, agent, name, args, key):
        with self.e.read() as db:
            row = db.execute('SELECT s.*,t.agent,t.channel,t.actor FROM p_steps s JOIN p_tasks t ON t.id=s.task AND t.tenant=s.tenant '
                             'WHERE s.tenant=? AND s.id=?', (tenant,key)).fetchone()
            if not row or row['tool'] != name or row['agent'] != agent or json.loads(row['args']) != args:
                raise Forbidden('Google tool requires matching active engine step')
        current = self.e._validated(tenant,agent,[{'tool':name,'args':args}])[0]
        if current[4] != row['fingerprint']:
            raise Forbidden('Google approval configuration changed')
        if not self.e.dispatch_allowed(tenant, row): raise Forbidden('Step dispatch no longer authorized')
        return row

    def execute(self, tenant, connection, agent, name, args, key):
        self.e.registry.get(name).validate(args)
        method, url, body, reference = authorize_args(name, args, self.resources, key)
        self._active_step(tenant,agent,name,args,key)  # No token refresh for an unclaimed/unapproved call.
        grant = self.m.access(tenant, connection, agent, [SCOPES[name]])
        row = self._active_step(tenant,agent,name,args,key)
        self.m.fence(tenant,connection,agent,[SCOPES[name]],grant)
        write = name in WRITES
        if write:
            with self.e.tx() as db:
                # Re-check in the dispatch transaction, not just before token IO.
                current_binding = self.e._validated(tenant,agent,[{'tool':name,'args':args}])[0]
                if current_binding[4] != row['fingerprint']:
                    raise Forbidden('Google policy changed before dispatch')
                credential = self.m._agent(db,tenant,connection,agent,[SCOPES[name]])
                self.e.require_task_parent(db,tenant,row['task'])
                self.e.require_authority(db,tenant,row['channel'],row['actor'])
                if row['approval_needed']:
                    approval = db.execute('SELECT actor,status,fingerprint,expires FROM p_approvals WHERE tenant=? AND step=?',(tenant,key)).fetchone()
                    required = ('owner',) if self.e.policy(tenant,agent).get('approver_role')=='owner' else ('owner','operator')
                    if not approval or approval['status']!='consumed' or approval['expires']<=self.e.clock() or approval['fingerprint']!=row['fingerprint']:
                        raise Forbidden('Current write approval required')
                    self.e.require_authority(db,tenant,'approval',approval['actor'],required)
                current = db.execute("SELECT 1 FROM p_steps WHERE tenant=? AND id=? AND claim=? AND status='running' AND lease>?",
                                     (tenant,key,row['claim'],self.e.clock())).fetchone()
                if not current or credential['status'] != 'active' or credential['generation'] != grant.generation:
                    raise Forbidden('Google dispatch fenced')
                if db.execute('SELECT 1 FROM p_google_dispatch WHERE tenant=? AND step=?',(tenant,key)).fetchone():
                    raise Conflict('Google write already dispatched; reconcile outcome')
                db.execute('INSERT INTO p_google_dispatch VALUES(?,?,?,?,?,?,?,?,?)',
                           (tenant,key,connection,grant.generation,digest({'url':url,'body':body,'step_fingerprint':row['fingerprint'],
                               'connection':connection,'generation':grant.generation,'config':self.m.config_hash,
                               'resources':digest(self.resources)}), 'dispatching',encode(reference),self.e.clock(),self.e.clock()))
        try:
            # Last authorization fence. A provider request already in flight
            # cannot be atomically recalled by a local database revocation.
            self.m.fence(tenant,connection,agent,[SCOPES[name]],grant)
            response = self.transport(method,url,grant.token,body)
            if not isinstance(response,dict) or len(encode(response).encode())>1_000_000:
                raise OAuthError('Invalid bounded Google response')
            if write and (not isinstance(response.get('id'),str) or not response['id']):
                raise OAuthError('Provider write receipt missing')
            if write:
                bounded(response['id'],256)
                if name == 'google.calendar.create' and response['id'] != reference['event_id']:
                    raise OAuthError('Calendar receipt does not match requested event')
                receipt = {'provider': 'google', 'operation': name, 'external_id': response['id'], **reference}
                with self.e.tx() as db:
                    saved = db.execute("UPDATE p_google_dispatch SET status='succeeded',receipt=?,updated=? WHERE tenant=? AND step=? AND status='dispatching'",
                               (encode(receipt),self.e.clock(),tenant,key))
                    if saved.rowcount != 1: raise Conflict('Google dispatch was already reconciled or fenced')
                return receipt
            self.m.fence(tenant,connection,agent,[SCOPES[name]],grant)
            self._active_step(tenant,agent,name,args,key)
            return {'provider': 'google', 'operation': name, 'untrusted_content': True, 'data': response}
        except Exception:
            if write:
                with self.e.tx() as db:
                    db.execute("UPDATE p_google_dispatch SET status='uncertain',updated=? WHERE tenant=? AND step=? AND status='dispatching'",
                               (self.e.clock(),tenant,key))
            raise OAuthError('Google operation outcome unavailable') from None


def handler(name):
    def run(engine,tenant,agent,args,key):
        connection = args['connection']
        return GoogleAdapter(engine,configured_manager(engine,tenant,connection),configured_resources(tenant,connection)).execute(
            tenant,connection,agent,name,args,key)
    return run


def register_tools(registry):
    common = {'connection': string(128)}
    paging = {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'page_token': string(2000)}
    definitions = {
        'google.gmail.list': obj({**common,**paging,'query':string(500)}, ['connection']),
        'google.gmail.read': obj({**common,'id':string(256)}),
        'google.gmail.send': obj({**common,'to':string(256),'subject':string(200),'text':string(12000)}),
        'google.drive.list': obj({**common,**paging}, ['connection']),
        'google.drive.metadata': obj({**common,'id':string(256)}),
        'google.calendar.list': obj({**common,**paging,'calendar':string(256),'time_min':string(64)}, ['connection','calendar']),
        'google.calendar.create': obj({**common,'calendar':string(256),'summary':string(200),'description':string(8000),
                                      'start':string(64),'end':string(64)}, ['connection','calendar','summary','start','end']),
    }
    register_once(registry, [
        Tool(name, 'write' if name in WRITES else 'read', schema, handler(name),
             external=True)
        for name, schema in definitions.items()
    ])
