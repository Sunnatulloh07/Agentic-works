"""The dashboard's tool surface and the API meet here.

``apps/ui/lib/tools-client.mjs`` builds the request body (node-tested in isolation);
this file pins the CONTRACT over real HTTP: the body the surface builds is what
``POST /platform/{tenant}/tasks`` accepts, a read tool actually runs through the
worker, and the refusals the surface pre-flights (a tool outside the agent's policy,
an unknown tool name, out-of-bound arguments) are the refusals the server makes.

The tenant is the shipped ``turkish-baby`` pack, so the tools named here are the ones
a delivered pack declares: ``ops.assistant`` holds the read-only oversight surface
(``agent.activity`` / ``agent.cost`` / ``agent.health``).
"""
import os
os.environ.setdefault('ENV', 'test')
os.environ.setdefault('ALLOW_INSECURE_DEV', 'true')

import pytest
from fastapi.testclient import TestClient

from app import platform_api as api
from app.auth import issue_token
from app.main import app
from app.storage import reset
from platform_runtime.engine import Engine
from platform_runtime.oversight import MAX_EVENTS, MAX_WINDOW_SECONDS
from platform_runtime.tools import build_registry

TENANT = 'turkish-baby'
OPS = 'ops.assistant'
SALES = 'sales.assistant'


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    engine = Engine(tmp_path / 'engine.db', build_registry(), api.policy)
    engine.agent_catalog = api.agents  # the wiring api.engine() applies in production
    monkeypatch.setattr(api, 'engine', lambda: engine)
    with TestClient(app) as client:
        yield client, engine
    reset()


def body(agent, tool, args, key='surface-1'):
    """Exactly the shape tools-client.mjs toolCallBody() returns: one step."""
    return {'agent': agent, 'key': key, 'steps': [{'tool': tool, 'args': args}]}


def headers(role='operator'):
    return {'Authorization': 'Bearer ' + issue_token(TENANT, subject='olga', role=role)}


def test_a_read_call_from_the_surface_is_queued_and_then_runs(http):
    client, engine = http
    response = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                           json=body(OPS, 'agent.activity', {'agent': SALES, 'limit': 10}))
    assert response.status_code == 200
    task_id = response.json()['task_id']
    task = engine.get(TENANT, task_id)
    assert (task['agent'], task['status']) == (OPS, 'queued')
    assert task['steps'][0]['tool'] == 'agent.activity'
    assert engine.tick(TENANT) is True
    step = engine.get(TENANT, task_id)['steps'][0]
    assert step['status'] == 'succeeded'
    assert step['result']['agent'] == SALES
    assert step['result']['truncated'] is False


def test_the_bounds_the_surface_shows_are_the_bounds_the_server_enforces(http):
    client, _ = http
    accepted = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                           json=body(OPS, 'agent.activity',
                                     {'agent': SALES, 'limit': MAX_EVENTS,
                                      'since_seconds': MAX_WINDOW_SECONDS}, key='at-bound'))
    assert accepted.status_code == 200
    over = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                       json=body(OPS, 'agent.activity', {'agent': SALES, 'limit': MAX_EVENTS + 1},
                                 key='over-bound'))
    assert over.status_code == 422


def test_a_tool_outside_the_agents_policy_is_refused(http):
    client, _ = http
    response = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                           json=body(SALES, 'agent.health', {'agent': OPS}))
    assert response.status_code == 403


def test_an_unknown_tool_name_is_refused(http):
    client, _ = http
    response = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                           json=body(OPS, 'ghost.tool', {}))
    assert response.status_code == 422


def test_an_unknown_target_agent_is_refused_by_the_read_itself(http):
    """Oversight scopes the name to the pack: a typo is not an empty report.

    The step records the exception CLASS the runtime maps (``Forbidden``), not its
    free-text message -- the message names the pack to the caller, and the runtime
    keeps that out of the row.
    """
    client, engine = http
    response = client.post(f'/platform/{TENANT}/tasks', headers=headers(),
                           json=body(OPS, 'agent.health', {'agent': 'ghost.agent'}))
    assert response.status_code == 200
    assert engine.tick(TENANT) is True
    step = engine.get(TENANT, response.json()['task_id'])['steps'][0]
    assert (step['status'], step['error']) == ('failed', 'Forbidden')


def test_the_shipped_engine_wires_the_oversight_catalog(tmp_path, monkeypatch):
    """api.engine() attaches the pack listing, and that hook is what makes the
    refusal above reachable outside tests: without it oversight checks only the
    id's shape (the module's own fallback) and reports a typo as zeros."""
    monkeypatch.setenv('IDENTITY_DIRECTORY', 'false')
    monkeypatch.setenv('PIPELINE_MODE', 'platform')
    monkeypatch.setenv('APP_DB', str(tmp_path / 'app.db'))
    reset()
    engine = api.engine()
    assert callable(engine.agent_catalog)
    assert SALES in {entry['id'] for entry in engine.agent_catalog(TENANT)}
    assert 'ghost.agent' not in {entry['id'] for entry in engine.agent_catalog(TENANT)}
    reset()
