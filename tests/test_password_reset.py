import io
import json
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from test_app import register
from wagonhub import db, password_reset, security
from wagonhub.app import app


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'reset.db'))
    for name, value in {
        'UNISENDER_GO_API_KEY': 'fake-key-for-tests',
        'RESET_FROM_EMAIL': 'no-reply@example.com',
        'PASSWORD_RESET_SECRET': 'test-only-secret-' * 4,
    }.items():
        monkeypatch.setenv(name, value)
    clock = [10000]
    monkeypatch.setattr(password_reset.time, 'time', lambda: clock[0])
    messages = []
    monkeypatch.setattr(password_reset, 'send_email', lambda *args: messages.append(args) or True)
    with TestClient(app, headers={'X-WagonHub-Request': '1'}) as client:
        yield client, clock, messages


def send(client, step, email='master@example.com', **fields):
    return client.post('/api/password-reset/'+step, json={'email': email, **fields})


def test_master_recovers_without_changing_profile(recovery, monkeypatch):
    c, clock, messages = recovery
    email = 'master@example.com'
    old_session = register(c, 'track', email)
    before = c.get('/api/me', headers=old_session).json()
    monkeypatch.setattr(security, 'production', lambda: True)
    monkeypatch.setenv('PILOT_ADMIN_EMAIL', email)
    monkeypatch.setenv('PILOT_INVITES_JSON', '{"master@example.com":{"role":"track","code":"MMMMMMMMMMMMMMMMMMMMMMMMMMMM"}}')
    monkeypatch.setenv('YANDEX_METRIKA_ID', '12345')
    assert 'window.WAGONHUB_METRIKA_ID=null' in c.get('/reset-password').text
    missing = send(c, 'request', email='unknown@example.com')
    created = send(c, 'request')
    assert (missing.status_code, missing.json()) == (created.status_code, created.json())
    assert created.status_code == 202 and created.headers['cache-control'] == 'no-store'
    assert send(c, 'request').status_code == 202
    assert len(messages) == 1 and messages[0][2] == email
    code = messages[0][-1]
    assert len(code) == 8 and code.isdigit() and code not in created.text
    with db.transaction() as con:
        active = con.execute('SELECT code_hash FROM password_resets').fetchone()
        assert code not in active['code_hash']
    good = send(c, 'verify', code=code)
    assert good.status_code == 200 and good.headers['cache-control'] == 'no-store'
    token = good.json()['token']
    assert send(c, 'verify', code=code).status_code == 400
    assert send(c, 'complete', email='unknown@example.com', token=token, password='new-long-password').status_code == 400
    assert send(c, 'complete', token=token, password='new-long-password').status_code == 204
    assert send(c, 'complete', token=token, password='not-the-password').status_code == 400
    assert c.get('/api/me', headers=old_session).status_code == 401
    assert c.post('/api/login', json={'email': email, 'password': 'securepassword'}).status_code == 401
    login = c.post('/api/login', json={'email': email, 'password': 'new-long-password'})
    assert login.status_code == 200
    user = login.json()['user']
    assert user['id'] == before['id'] and user['role'] == before['role'] and user['is_admin']


def test_cooldown_rotation_and_attempt_limits(recovery):
    c, clock, messages = recovery
    register(c, 'track', 'master@example.com')
    assert send(c, 'request').status_code == 202
    old = messages[-1][-1]
    clock[0] += 59
    assert send(c, 'request').status_code == 202 and len(messages) == 1
    clock[0] += 1
    # Force a distinct replacement code so the test does not rely on randomness.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(password_reset.secrets, 'randbelow', lambda _: (int(old)+1) % 100_000_000)
        assert send(c, 'request').status_code == 202
    assert len(messages) == 2
    new = messages[-1][-1]
    for _ in range(5):
        assert send(c, 'verify', code=old).status_code == 400
    assert send(c, 'verify', code=new).status_code == 400
    assert send(c, 'request').status_code == 429


def test_expiry_invalid_request_and_failed_send(recovery, monkeypatch):
    c, clock, messages = recovery
    register(c, 'track', 'master@example.com')
    assert c.post('/api/password-reset/request', json={'email':'master@example.com'},
                  headers={'X-WagonHub-Request':''}).status_code == 403
    invalid = send(c, 'verify', code='secret-dont-reflect')
    assert invalid.status_code == 422 and 'secret-dont-reflect' not in invalid.text
    assert 'Введите 8 цифр' in invalid.text
    assert send(c, 'request').status_code == 202
    clock[0] += 600
    assert send(c, 'verify', code=messages[-1][-1]).status_code == 400
    assert send(c, 'request').status_code == 202
    token = send(c, 'verify', code=messages[-1][-1]).json()['token']
    clock[0] += 600
    assert send(c, 'complete', token=token, password='new-long-password').status_code == 400
    monkeypatch.setattr(password_reset, 'send_email', lambda *args: False)
    assert send(c, 'request').status_code == 202
    with db.transaction() as con:
        assert con.execute('SELECT code_hash FROM password_resets').fetchone()['code_hash'] is None
    monkeypatch.delenv('PASSWORD_RESET_SECRET')
    assert send(c, 'request').status_code == 503
    assert 'window.WAGONHUB_PASSWORD_RESET_ENABLED=false' in c.get('/reset-password').text


@pytest.mark.parametrize('result, expected', [({'status':'success','job_id':'demo'}, True),
                                              ({'status':'success','failed_emails':{'x':'invalid'}}, False),
                                              (HTTPError('https://example.invalid', 403, '', {}, None), False)])
def test_unisender_request_contract(monkeypatch, result, expected):
    class Transport:
        def open(self, req, timeout):
            assert req.full_url == password_reset.API and req.get_method() == 'POST'
            assert req.get_header('X-api-key') == 'fake-api-key' and timeout == 6
            body = json.loads(req.data)['message']
            assert body['recipients'] == [{'email':'to@example.com'}]
            assert body['from_email'] == 'from@example.com'
            assert '12340000' in body['body']['plaintext']
            assert body['idempotence_key'] == 'request-id'
            if isinstance(result, HTTPError):
                raise result
            return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(password_reset.request, 'build_opener', lambda *args: Transport())
    assert password_reset.send_email('fake-api-key','from@example.com','to@example.com','request-id','12340000') is expected
