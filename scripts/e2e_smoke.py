"""End-to-end smoke test: the real API + worker against a fake model and a fake Telegram.

    python scripts/e2e_smoke.py            # PASS/SKIP/FAIL per scenario, exit 1 on any FAIL
    python scripts/e2e_smoke.py --keep     # keep the temp directory (logs, app.db) for inspection

Nothing in the repository is written. Everything lives in a temp directory:
the database, an integrations file for demo-retail and the process logs. The
model and the Bot API are two stdlib HTTP servers on 127.0.0.1, reached through
the platform's own `provider_mode: local_loopback` + `base_url` settings, so
the code path is the production one (webhook -> inbox -> conversation turn ->
agent loop -> planner call -> tool task -> grounded reply -> telegram.send).

The fake model is scripted, not clever: it reads the planner context the
platform sends (observations, current customer message) and answers the way a
well-behaved model would. What is being tested is the platform around it.
Stdlib only; the API's own dependencies (fastapi, uvicorn, pydantic, PyYAML,
PyJWT) must be installed, as for scripts/run_local.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
TENANT = 'demo-retail'
CHAT_ID = 700100200
OWNER_EMAIL = 'owner@example.com'
OWNER_PASSWORD = 'E2e-Smoke-Passw0rd-1'
TG_TOKEN = '123456789:E2E_smoke_token_value'
WAIT_SECONDS = 60
CURRENT = 'Current customer message:\n'


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# --- fakes --------------------------------------------------------------------

class FakeModel:
    """Anthropic Messages API shape; scripted by the customer message and observations."""

    def __init__(self):
        self.requests: list[dict] = []

    @staticmethod
    def customer_text(context: dict) -> str:
        text = context.get('input', '')
        return text.split(CURRENT, 1)[1] if CURRENT in text else text

    def decide(self, context: dict) -> dict:
        text = self.customer_text(context).lower()
        observations = context.get('observations') or []
        tools = {t.get('name') for t in context.get('tools') or []}
        last = observations[-1] if observations else None
        if 'narxini o‘ylab top' in text:
            # A model that invents a price: the grounding gate must refuse it.
            return {'action': 'final', 'answer': 'Bu shim 777777 so‘m turadi.', 'evidence_ids': []}
        if 'buyurtma' in text and 'orders.draft' in tools:
            if last is None:
                return {'action': 'tool', 'tool': 'orders.draft',
                        'args': {'product_id': 'KB004', 'size': '4', 'qty': 2, 'customer_name': 'Dilnoza',
                                 'phone': '+998 90 123 45 67', 'delivery': 'chilonzor'}}
            result = last.get('result') or {}
            draft = result.get('draft') or {}
            if result.get('valid'):
                return {'action': 'final', 'evidence_ids': [last['evidence_id']],
                        'answer': f"Buyurtmangiz qabul qilindi: {draft.get('product_name')}, "
                                  f"{draft.get('qty')} dona, jami {draft.get('total_uzs')} so‘m. "
                                  'Operator tasdiqlagach xabar beramiz.'}
            return {'action': 'ask', 'question': 'Iltimos, o‘lcham va telefon raqamingizni yozing.'}
        if last is None:
            return {'action': 'tool', 'tool': 'products.search', 'args': {'query': 'shim jinse'}}
        products = (last.get('result') or {}).get('products') or []
        if not products:
            return {'action': 'ask', 'question': 'Qaysi mahsulotni qidiryapsiz?'}
        p = products[0]
        return {'action': 'final', 'evidence_ids': [last['evidence_id']],
                'answer': f"Ha, {p['name']} bor. Narxi {p['price_uzs']} so‘m. O‘lchamlar: "
                          + ', '.join(str(s) for s in p.get('sizes') or []) + '.'}

    def handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)))
                model.requests.append(body)
                try:
                    context = json.loads(body['messages'][0]['content'])
                    decision = model.decide(context)
                except Exception as exc:  # noqa: BLE001 - a broken fake must be visible
                    decision = {'action': 'ask', 'question': f'fake model error {type(exc).__name__}'}
                reply = {'id': 'msg_' + uuid.uuid4().hex, 'type': 'message', 'role': 'assistant',
                         'model': body.get('model'), 'stop_reason': 'end_turn',
                         'content': [{'type': 'text', 'text': json.dumps(decision, ensure_ascii=False)}],
                         'usage': {'input_tokens': 100, 'output_tokens': 50}}
                raw = json.dumps(reply, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        return Handler


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.lock = threading.Lock()

    def handler(self):
        tg = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)))
                with tg.lock:
                    tg.sent.append({'path_ok': self.path.endswith('/sendMessage'), **body})
                    message_id = len(tg.sent)
                raw = json.dumps({'ok': True, 'result': {'message_id': message_id}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        return Handler

    def to_chat(self, chat_id) -> list[str]:
        with self.lock:
            return [m.get('text', '') for m in self.sent if str(m.get('chat_id')) == str(chat_id)]


def serve(handler) -> tuple[ThreadingHTTPServer, int]:
    port = free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


# --- API client -----------------------------------------------------------------

class Api:
    def __init__(self, base: str):
        self.base = base
        self.token = ''

    def call(self, method: str, path: str, body=None, headers=None, token=True):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        h = {'Content-Type': 'application/json', **(headers or {})}
        if token and self.token:
            h['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as r:
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode('utf-8', 'replace')

    def login(self):
        status, body = self.call('POST', '/identity/login', {'email': OWNER_EMAIL, 'password': OWNER_PASSWORD}, token=False)
        if status != 200:
            raise RuntimeError(f'login {status}: {body}')
        account = body['tokens']['access_token']
        status, ws = self.call('POST', f'/identity/workspaces/{TENANT}/select', {},
                               headers={'Authorization': 'Bearer ' + account}, token=False)
        if status != 200:
            raise RuntimeError(f'workspace select {status}: {ws}')
        self.token = ws['access_token']

    def p(self, path: str) -> str:
        return f'/platform/{TENANT}{path}'


def wait_for(predicate, seconds=WAIT_SECONDS, step=0.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


# --- scenarios --------------------------------------------------------------------

class Smoke:
    def __init__(self, api: Api, tg: FakeTelegram, model: FakeModel, secret: str):
        self.api, self.tg, self.model, self.secret = api, tg, model, secret
        self.update_id = 5000
        self.results: list[tuple[str, str, str]] = []

    def record(self, name, status, detail=''):
        self.results.append((name, status, detail))
        print(f'{status:4}  {name}' + (f': {detail}' if detail else ''), flush=True)

    def customer_says(self, text: str, chat_id=CHAT_ID):
        self.update_id += 1
        update = {'update_id': self.update_id, 'message': {
            'message_id': self.update_id, 'date': int(time.time()),
            'from': {'id': chat_id, 'is_bot': False, 'first_name': 'Dilnoza'},
            'chat': {'id': chat_id, 'type': 'private'}, 'text': text}}
        return self.api.call('POST', f'/webhooks/telegram?tenant={TENANT}', update,
                             headers={'X-Telegram-Bot-Api-Secret-Token': self.secret}, token=False)

    def next_reply(self, before: int, chat_id=CHAT_ID):
        replies = wait_for(lambda: self.tg.to_chat(chat_id)[before:] or None)
        return replies[0] if replies else None

    def run(self):
        status, body = self.api.call('GET', '/health', token=False)
        self.record('health', 'PASS' if status == 200 else 'FAIL', str(body))
        try:
            self.api.login()
            self.record('owner login + workspace select', 'PASS')
        except Exception as exc:  # noqa: BLE001
            self.record('owner login + workspace select', 'FAIL', str(exc)[:300])
            return
        status, body = self.customer_says('')
        ignored = status == 200 and isinstance(body, dict) and body.get('ignored') is True
        self.record('empty message ignored without an event', 'PASS' if ignored else 'FAIL', f'{status} {body}')
        self.product_question()
        self.invented_price_is_refused()
        self.order()
        self.operator_reply()
        self.bad_secret()

    def product_question(self):
        before = len(self.tg.to_chat(CHAT_ID))
        status, body = self.customer_says('Salom, jinsi shim bormi? Narxi qancha?')
        if status != 200:
            return self.record('product question accepted', 'FAIL', f'{status} {body}')
        reply = self.next_reply(before)
        if reply is None:
            return self.record('product question answered', 'FAIL', 'no sendMessage within timeout')
        grounded = '150000' in reply.replace(' ', '')
        self.record('product question answered from the catalogue', 'PASS' if grounded else 'FAIL', reply[:160])

    def invented_price_is_refused(self):
        before = len(self.tg.to_chat(CHAT_ID))
        self.customer_says('Shu shimning narxini o‘ylab top')
        reply = self.next_reply(before)
        if reply is None:
            return self.record('invented price refused', 'FAIL', 'no reply (customer must never be ignored)')
        self.record('invented price refused, handoff text sent', 'PASS' if '777777' not in reply else 'FAIL', reply[:160])
        status, body = self.api.call('GET', self.api.p('/handoffs'))
        if status == 404:
            return self.record('handoff visible to operator', 'SKIP', '/handoffs not available')
        items = body.get('handoffs', body) if isinstance(body, dict) else body
        found = status == 200 and any('o‘ylab top' in json.dumps(i, ensure_ascii=False) for i in items or [])
        self.record('handoff visible to operator', 'PASS' if found else 'FAIL', f'{status}')

    def order(self):
        before = len(self.tg.to_chat(CHAT_ID))
        self.customer_says('Buyurtma: KB004, 4 razmer, 2 dona, Dilnoza, +998 90 123 45 67, Chilonzor filiali')
        reply = self.next_reply(before)
        if reply is None:
            return self.record('order confirmed to customer', 'FAIL', 'no reply')
        self.record('order confirmed to customer with server-side total',
                    'PASS' if '300000' in reply.replace(' ', '') else 'FAIL', reply[:160])

        def pending_order():
            status, body = self.api.call('GET', self.api.p('/approvals?status=pending'))
            if status != 200:
                return None
            items = body.get('approvals', body) if isinstance(body, dict) else body
            return next((a for a in items or [] if a.get('tool') == 'records.create'
                         and 'KB004' in json.dumps(a.get('args'), ensure_ascii=False)), None)
        approval = wait_for(pending_order, seconds=20)
        if approval is None:
            return self.record('order waits for operator approval', 'SKIP',
                               'no records.create approval (pack has no order_agent?)')
        self.record('order waits for operator approval', 'PASS', approval.get('agent', ''))
        step = approval.get('step_id') or approval.get('step')
        status, body = self.api.call('POST', self.api.p(f'/steps/{step}/approval'), {'decision': 'approved'})
        if status != 200:
            return self.record('operator approves order', 'FAIL', f'{status} {body}')

        def stored():
            status, body = self.api.call('GET', self.api.p('/orders'))
            return status == 200 and 'KB004' in json.dumps(body, ensure_ascii=False)
        self.record('approved order listed in /orders', 'PASS' if wait_for(stored, seconds=20) else 'FAIL')

    def operator_reply(self):
        before = len(self.tg.to_chat(CHAT_ID))
        status, body = self.api.call('POST', self.api.p(f'/conversations/telegram/{CHAT_ID}/reply'),
                                     {'text': 'Assalomu alaykum, operator Madina. Yordam beraymi?'},
                                     headers={'Idempotency-Key': str(uuid.uuid4())})
        if status == 404 and 'Not Found' in json.dumps(body):
            return self.record('operator reply reaches the customer', 'SKIP', 'reply endpoint not available')
        if status not in (200, 201, 202):
            return self.record('operator reply accepted', 'FAIL', f'{status} {body}')
        reply = self.next_reply(before)
        self.record('operator reply reaches the customer', 'PASS' if reply and 'Madina' in reply else 'FAIL',
                    (reply or 'no sendMessage')[:120])
        self.takeover_pauses_the_bot()
        status, body = self.api.call('POST', self.api.p('/conversations/telegram/999000111/reply'),
                                     {'text': 'x'}, headers={'Idempotency-Key': str(uuid.uuid4())})
        self.record('operator reply to an unknown chat refused', 'PASS' if status in (403, 404) else 'FAIL', str(status))

    def takeover_pauses_the_bot(self):
        before = len(self.tg.to_chat(CHAT_ID))
        self.customer_says('Salom, jinsi shim bormi?')
        quiet = wait_for(lambda: len(self.tg.to_chat(CHAT_ID)) > before, seconds=8) is None
        self.record('bot stays quiet while the operator holds the chat', 'PASS' if quiet else 'FAIL',
                    '' if quiet else self.tg.to_chat(CHAT_ID)[-1][:120])
        status, body = self.api.call('POST', self.api.p(f'/conversations/telegram/{CHAT_ID}/release'), {},
                                     headers={'Idempotency-Key': str(uuid.uuid4())})
        if status != 200:
            return self.record('chat handed back to the bot', 'FAIL', f'{status} {body}')
        before = len(self.tg.to_chat(CHAT_ID))
        self.customer_says('Salom, jinsi shim bormi?')
        reply = self.next_reply(before)
        self.record('chat handed back to the bot, bot answers again',
                    'PASS' if reply and '150000' in reply.replace(' ', '') else 'FAIL', (reply or 'no reply')[:120])

    def bad_secret(self):
        status, _ = self.api.call('POST', f'/webhooks/telegram?tenant={TENANT}', {'update_id': 1, 'message': {'text': 'x'}},
                                  headers={'X-Telegram-Bot-Api-Secret-Token': 'wrong'}, token=False)
        self.record('webhook with a wrong secret refused', 'PASS' if status == 401 else 'FAIL', str(status))


# --- orchestration ------------------------------------------------------------------

def stop_tree(process: subprocess.Popen) -> None:
    """Stop run_local AND its uvicorn/worker children (terminate() alone orphans them on Windows)."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True)
    else:
        process.send_signal(__import__('signal').SIGINT)  # run_local forwards it to both children
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def build_env(tmp: Path, model_port: int, tg_port: int, api_port: int) -> dict:
    integrations = {TENANT: {
        'llm': {'provider': 'anthropic', 'provider_mode': 'local_loopback',
                'base_url': f'http://127.0.0.1:{model_port}', 'model': 'fake-claude',
                'agent_loop_enabled': True},
        'telegram': {'token_env': 'E2E_TG_TOKEN', 'provider_mode': 'local_loopback',
                     'base_url': f'http://127.0.0.1:{tg_port}'},
    }}
    (tmp / 'integrations.json').write_text(json.dumps(integrations), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in (
        'ALLOW_INSECURE_DEV', 'TELEGRAM_DEFAULT_TENANT', 'TENANT_SECRETS', 'HTTPS_PROXY', 'HTTP_PROXY')}
    env.update({
        'ENV': 'dev', 'PIPELINE_MODE': 'platform', 'IDENTITY_DIRECTORY': 'true', 'IDENTITY_BOOTSTRAP_ENABLED': 'false',
        'JWT_SECRET': secrets.token_urlsafe(48), 'ADMIN_TOKEN': secrets.token_urlsafe(32),
        'TELEGRAM_WEBHOOK_SECRET': secrets.token_urlsafe(24), 'META_VERIFY_TOKEN': secrets.token_urlsafe(24),
        'CORS_ORIGINS': 'http://localhost:3000', 'APP_DB': str(tmp / 'app.db'), 'PACKS_DIR': str(ROOT / 'packs'),
        'PLATFORM_INTEGRATIONS_FILE': str(tmp / 'integrations.json'), 'TRACE_PATH': str(tmp / 'trace.jsonl'),
        'OUTBOX_PATH': str(tmp / 'outbox'), 'E2E_TG_TOKEN': TG_TOKEN, 'PYTHONIOENCODING': 'utf-8',
        'NO_PROXY': '127.0.0.1,localhost',
    })
    return env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--keep', action='store_true', help='keep the temp directory')
    args = parser.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix='agent-platform-e2e-'))
    model, tg = FakeModel(), FakeTelegram()
    model_server, model_port = serve(model.handler())
    tg_server, tg_port = serve(tg.handler())
    api_port = free_port()
    env = build_env(tmp, model_port, tg_port, api_port)
    none_env = tmp / 'none.env'  # absent on purpose: the process environment is the configuration
    provision = subprocess.run(
        [sys.executable, str(SCRIPTS / 'provision_identity.py'), '--workspace', TENANT, '--workspace-name', 'Demo',
         '--email', OWNER_EMAIL, '--display-name', 'E2E Owner', '--password-stdin', '--env-file', str(none_env)],
        input=OWNER_PASSWORD + '\n', capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
    if provision.returncode != 0:
        print('FAIL  provision owner:', (provision.stdout + provision.stderr)[-500:])
        return 1
    log = open(tmp / 'platform.log', 'w', encoding='utf-8')
    runner = subprocess.Popen([sys.executable, str(SCRIPTS / 'run_local.py'), '--port', str(api_port),
                               '--env-file', str(none_env)], env=env, stdout=log, stderr=subprocess.STDOUT)
    api = Api(f'http://127.0.0.1:{api_port}')
    code = 1
    try:
        def healthy():
            if runner.poll() is not None:
                return 'dead'
            try:
                return api.call('GET', '/health', token=False)[0] == 200
            except OSError:  # not listening yet
                return False
        up = wait_for(healthy, seconds=90)
        if up != True:  # noqa: E712 - 'dead' is truthy too
            print('FAIL  platform start; see', tmp / 'platform.log')
            return 1
        smoke = Smoke(api, tg, model, env['TELEGRAM_WEBHOOK_SECRET'])
        smoke.run()
        leaked = [m for m in tg.sent if TG_TOKEN in json.dumps(m)]
        log.flush()
        in_log = TG_TOKEN in (tmp / 'platform.log').read_text(encoding='utf-8', errors='replace')
        smoke.record('bot token never in messages or logs', 'FAIL' if leaked or in_log else 'PASS')
        failed = [name for name, status, _ in smoke.results if status == 'FAIL']
        skipped = [name for name, status, _ in smoke.results if status == 'SKIP']
        print(f"e2e: {len(smoke.results) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, "
              f"{len(failed)} failed; model calls {len(model.requests)}, telegram sends {len(tg.sent)}")
        code = 1 if failed else 0
    finally:
        stop_tree(runner)
        log.close()
        model_server.shutdown(); tg_server.shutdown()
        if args.keep or code:
            print('e2e: artifacts kept in', tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
