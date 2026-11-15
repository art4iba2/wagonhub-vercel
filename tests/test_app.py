import pytest
from fastapi.testclient import TestClient
from wagonhub import db, security
from wagonhub.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    with TestClient(app, headers={'X-WagonHub-Request': '1'}) as test:
        yield test


def register(client, role, email):
    response = client.post('/api/register', json={'name': 'Тестовый пользователь', 'email': email,
                                                   'password': 'securepassword', 'role': role})
    assert response.status_code == 201, response.text
    client.cookies.clear()
    return session_header(response)


def session_header(response):
    return {'Cookie': f'{security.COOKIE}={response.cookies[security.COOKIE]}'}


def listing(client, headers, **changes):
    data = {'title': 'Станция Центральная', 'city': 'Москва', 'address': 'Складская, 5',
            'capacity': 3, 'price': '1200.50', 'description': 'Удобный подъезд'}
    data.update(changes)
    return client.post('/api/listings', json=data, headers=headers)


def company(client, headers, inn='1234567890'):
    return client.post('/api/company', json={'name': 'ООО Вагоны', 'inn': inn,
                                             'email': 'office@example.com', 'phone': '+7 900 000'}, headers=headers)


def test_frontend_and_auth(client):
    assert client.get('/').status_code == 200
    assert 'ВагонМесто' in client.get('/').text
    assert 'data-goal="landing_find_places"' in client.get('/static/app.js').text
    assert 'window.WAGONHUB_METRIKA_ID=null' in client.get('/').text
    assert 'mc.yandex.ru/metrika/tag.js' in client.get('/static/analytics.js').text
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/api/me').status_code == 401
    track = register(client, 'track', 'track@example.com')
    assert client.get('/api/me', headers=track).json()['role'] == 'track'
    assert client.post('/api/register', json={'name': 'Повтор', 'email': 'TRACK@example.com',
                                              'password': 'securepassword', 'role': 'track'}).status_code == 409
    assert client.post('/api/login', json={'email': 'track@example.com', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'email': 'nobody@example.com', 'password': 'wrong'}).status_code == 401
    login = client.post('/api/login', json={'email': 'TRACK@example.com', 'password': 'securepassword'})
    assert login.status_code == 200
    assert client.get('/api/me', headers={'Cookie': f'{security.COOKIE}=invalid'}).status_code == 401
    assert client.get('/api/me', headers={'Cookie': 'other=abc'}).status_code == 401
    assert client.post('/api/logout', headers=track).status_code == 204
    assert client.get('/api/me', headers=track).status_code == 401
    assert client.get('/api/me', headers=session_header(login)).status_code == 200


def test_metrika_configuration(client, monkeypatch):
    monkeypatch.setenv('YANDEX_METRIKA_ID', '12345678')
    response = client.get('/')
    assert response.status_code == 200
    assert 'window.WAGONHUB_METRIKA_ID=12345678' in response.text
    assert '__YANDEX_METRIKA_ID__' not in response.text
    monkeypatch.setenv('YANDEX_METRIKA_ID', '12</script>')
    response = client.get('/')
    assert response.status_code == 200
    assert 'window.WAGONHUB_METRIKA_ID=null' in response.text
    assert '12</script>' not in response.text


def test_roles_companies_and_validation(client):
    track = register(client, 'track', 'track@example.com')
    wagon = register(client, 'wagon', 'wagon@example.com')
    assert client.post('/api/company', json={'name': 'ООО X', 'inn': '1234567890', 'email': 'a@b.com'}, headers=track).status_code == 403
    assert client.get('/api/company', headers=track).status_code == 403
    assert client.get('/api/company', headers=wagon).json() is None
    assert company(client, wagon).status_code == 201
    assert client.get('/api/company', headers=wagon).json()['inn'] == '1234567890'
    assert company(client, wagon).status_code == 409
    another = register(client, 'wagon', 'other@example.com')
    assert company(client, another).status_code == 409
    assert client.post('/api/company', json={'name': 'X', 'inn': 'broken', 'email': 'invalid'}, headers=another).status_code == 422
    assert listing(client, wagon).status_code == 403
    assert listing(client, track, capacity=0).status_code == 422
    assert listing(client, track, city='  ').status_code == 422
    assert client.get('/api/my/listings', headers=wagon).status_code == 403
    assert client.post('/api/deals', json={'listing_id': 1, 'wagons': 1}, headers=track).status_code == 403
    assert client.post('/api/deals/1/decision', json={'status': 'confirmed'}, headers=wagon).status_code == 403
    assert client.get('/api/my/deals', headers=wagon).json() == []


def test_listing_filters_withdraw_and_ownership(client):
    track = register(client, 'track', 'track@example.com')
    other = register(client, 'track', 'other@example.com')
    first = listing(client, track).json()['id']
    second = listing(client, track, city='Казань', price='4000.00', capacity=8).json()['id']
    assert len(client.get('/api/listings').json()) == 2
    assert client.get('/api/listings?city=моск').json()[0]['id'] == first
    assert client.get('/api/listings?city=%25').json() == []
    assert client.get('/api/listings?min_capacity=4').json()[0]['id'] == second
    assert client.get('/api/listings?max_price=2000').json()[0]['id'] == first
    assert client.get('/api/listings?min_capacity=0').status_code == 422
    assert client.get('/api/my/listings', headers=track).json()[0]['id'] == second
    assert client.post('/api/listings/999/withdraw', headers=track).status_code == 404
    assert client.post(f'/api/listings/{first}/withdraw', headers=other).status_code == 403
    assert client.post(f'/api/listings/{first}/withdraw', headers=track).json()['status'] == 'withdrawn'
    assert client.post(f'/api/listings/{first}/withdraw', headers=track).status_code == 409
    assert len(client.get('/api/listings').json()) == 1


def test_deal_lifecycle_and_capacity(client):
    track = register(client, 'track', 'track@example.com')
    wagon = register(client, 'wagon', 'wagon@example.com')
    other = register(client, 'wagon', 'other@example.com')
    outsider = register(client, 'track', 'outsider@example.com')
    lid = listing(client, track).json()['id']
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 1}, headers=wagon).status_code == 409
    assert company(client, wagon).status_code == 201
    assert company(client, other, '123456789012').status_code == 201
    assert client.post('/api/deals', json={'listing_id': 999, 'wagons': 1}, headers=wagon).status_code == 404
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 4}, headers=wagon).status_code == 409
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 0}, headers=wagon).status_code == 422
    deal = client.post('/api/deals', json={'listing_id': lid, 'wagons': 2}, headers=wagon)
    assert deal.status_code == 201
    did = deal.json()['id']
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 1}, headers=wagon).status_code == 409
    second = client.post('/api/deals', json={'listing_id': lid, 'wagons': 2}, headers=other).json()['id']
    assert len(client.get('/api/my/deals', headers=track).json()) == 2
    assert len(client.get('/api/my/deals', headers=wagon).json()) == 1
    assert client.post('/api/deals/999/decision', json={'status': 'confirmed'}, headers=track).status_code == 404
    assert client.post(f'/api/deals/{did}/decision', json={'status': 'confirmed'}, headers=outsider).status_code == 403
    assert client.post(f'/api/deals/{did}/decision', json={'status': 'confirmed'}, headers=track).status_code == 200
    assert client.get('/api/listings').json()[0]['free_capacity'] == 1
    assert client.post(f'/api/deals/{did}/decision', json={'status': 'rejected'}, headers=track).status_code == 409
    assert client.post(f'/api/deals/{second}/decision', json={'status': 'confirmed'}, headers=track).status_code == 409
    assert client.post(f'/api/deals/{second}/decision', json={'status': 'rejected'}, headers=track).status_code == 200
    assert client.get('/api/my/deals', headers=other).json()[0]['status'] == 'rejected'
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 2}, headers=other).status_code == 409
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 1}, headers=other).status_code == 201
    assert client.post(f'/api/listings/{lid}/withdraw', headers=track).status_code == 200
    assert client.get('/api/my/deals', headers=other).json()[0]['status'] == 'rejected'
    assert client.post('/api/deals', json={'listing_id': lid, 'wagons': 1}, headers=wagon).status_code == 404
    assert client.get('/api/my/deals', headers=wagon).json()[0]['status'] == 'confirmed'


def test_applicant_contacts_and_edit_listing(client):
    owner = register(client, 'track', 'owner@example.com')
    stranger = register(client, 'track', 'stranger@example.com')
    applicant = register(client, 'wagon', 'applicant@example.com')
    assert company(client, applicant).status_code == 201
    lid = listing(client, owner).json()['id']
    original = {'title': 'Новая площадка', 'city': 'Тверь', 'address': 'Промышленная, 9',
                'capacity': 4, 'price': '1800.00', 'description': 'Круглосуточный доступ'}
    path = f'/api/listings/{lid}'
    assert client.put(path, json=original).status_code == 401
    assert client.put(path, json=original, headers=applicant).status_code == 403
    assert client.put('/api/listings/999', json=original, headers=owner).status_code == 404
    assert client.put(path, json=original, headers=stranger).status_code == 403
    assert client.put(path, json={**original, 'capacity': 0}, headers=owner).status_code == 422
    assert client.put(path, json=original, headers=owner).json() == {'id': lid, 'status': 'available'}
    edited = client.get('/api/listings?city=тверь').json()[0]
    assert edited['title'] == 'Новая площадка'
    assert edited['price'] == 1800.0

    request = client.post('/api/deals', json={'listing_id': lid, 'wagons': 3}, headers=applicant)
    assert request.status_code == 201
    deal_id = request.json()['id']
    assert client.get('/api/listings').json()[0].get('wagon_email') is None
    assert client.get('/api/my/deals').status_code == 401
    assert client.get('/api/my/deals', headers=stranger).json() == []
    incoming = client.get('/api/my/deals', headers=owner).json()[0]
    assert incoming['wagon_name'] == 'Тестовый пользователь'
    assert incoming['wagon_email'] == 'applicant@example.com'
    assert incoming['company_name'] == 'ООО Вагоны'
    assert incoming['company_inn'] == '1234567890'
    assert incoming['company_email'] == 'office@example.com'
    assert incoming['company_phone'] == '+7 900 000'

    assert client.post(f'/api/deals/{deal_id}/decision', json={'status': 'confirmed'}, headers=owner).status_code == 200
    assert client.put(path, json={**original, 'capacity': 2}, headers=owner).status_code == 409
    assert client.get('/api/listings').json()[0]['free_capacity'] == 1
    assert client.put(path, json={**original, 'capacity': 3, 'price': '2200.00'}, headers=owner).status_code == 200
    assert client.get('/api/my/deals', headers=owner).json()[0]['price'] == 1800.0
    assert client.get('/api/listings').json() == []  # all spaces are occupied
    assert client.post(f'{path}/withdraw', headers=owner).status_code == 200
    assert client.put(path, json=original, headers=owner).json()['status'] == 'withdrawn'
    assert client.get('/api/listings').json() == []


@pytest.mark.parametrize(('key', 'value'), [('VERCEL', '1'), ('APP_ENV', 'production')])
def test_production_requires_persistent_database(monkeypatch, key, value):
    monkeypatch.setattr(db, 'DATABASE_URL', '')
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        db.connect()


def test_postgres_adapter_and_transaction(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.statements = []
            self.closed = False

        def execute(self, sql, args=()):
            self.statements.append((sql, args))
            return self

        def fetchone(self):
            return {'id': 42}

        def close(self):
            self.closed = True

    connections = []

    def fake_connect(url, **kwargs):
        assert url == 'postgresql://example.invalid/test'
        assert kwargs['autocommit'] is True
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://example.invalid/test')
    monkeypatch.setattr(db.psycopg, 'connect', fake_connect)
    db.init_db()
    assert any('CREATE TABLE IF NOT EXISTS deals' in sql for sql, _ in connections[0].statements)
    assert connections[0].closed
    with db.transaction() as con:
        assert db.insert_id(con, 'INSERT INTO listings(title) VALUES (?)', ('Путь',)) == 42
        db.lock_listing(con, 42)
    assert ('INSERT INTO listings(title) VALUES (%s) RETURNING id', ('Путь',)) in connections[1].statements
    assert ('SELECT id FROM listings WHERE id=%s FOR UPDATE', (42,)) in connections[1].statements
    assert connections[1].statements[-1][0] == 'COMMIT'
    assert connections[1].closed
    with pytest.raises(ValueError), db.transaction() as con:
        raise ValueError('rollback')
    assert connections[2].statements[-1][0] == 'ROLLBACK'
    assert connections[2].closed


def test_pilot_invitation_csrf_and_rate_limit(client, monkeypatch):
    monkeypatch.setattr(security, 'production', lambda: True)
    monkeypatch.setattr(security.time, 'time', lambda: 1201)
    code = 'I' * 28
    monkeypatch.setenv('PILOT_INVITES_JSON',
                       f'{{"pilot@example.com":{{"role":"wagon","code":"{code}","inn":"1234567890"}}}}')
    fields = {'name': 'Участник пилота', 'email': 'pilot@example.com',
              'password': 'securepassword', 'role': 'wagon', 'invite_code': code}
    assert client.post('/api/register', json=fields, headers={'X-WagonHub-Request': ''}).status_code == 403
    assert client.post('/api/register', json={**fields, 'role': 'track'}).status_code == 403
    assert client.post('/api/register', json={**fields, 'invite_code': 'wrong'}).status_code == 403
    response = client.post('/api/register', json=fields)
    assert response.status_code == 201, response.text
    assert 'token' not in response.json()
    for attr in ('HttpOnly', 'Secure', 'SameSite=lax'):
        assert attr in response.headers['set-cookie']
    headers = session_header(response)
    assert company(client, headers, inn='1234567891').status_code == 403
    assert company(client, headers).status_code == 201
    assert client.post('/api/logout', headers={**headers, 'X-WagonHub-Request': ''}).status_code == 403
    for _ in range(8):
        assert client.post('/api/login', json={'email': fields['email'], 'password': 'wrong'}).status_code == 401
    limited = client.post('/api/login', json={'email': fields['email'], 'password': 'wrong'})
    assert limited.status_code == 429
    assert int(limited.headers['Retry-After']) > 0
    monkeypatch.setenv('PILOT_INVITES_JSON', '{}')
    assert client.get('/api/me', headers=headers).status_code == 401


def test_complete_releases_capacity_and_notifies(client):
    owner = register(client, 'track', 'track@example.com')
    other_owner = register(client, 'track', 'other@example.com')
    wagon = register(client, 'wagon', 'wagon@example.com')
    assert company(client, wagon).status_code == 201
    item = listing(client, owner, capacity=1).json()['id']
    deal = client.post('/api/deals', headers=wagon, json={'listing_id': item, 'wagons': 1}).json()['id']
    assert client.get('/api/my/deals', headers=wagon).json()[0]['track_email'] is None
    assert client.get('/api/my/notifications', headers=owner).json() == [{'deal_id': deal, 'event': 'pending'}]
    assert client.get('/api/my/notifications', headers=other_owner).json() == []
    path = f'/api/deals/{deal}'
    assert client.post(path+'/complete', headers=owner).status_code == 409
    assert client.post(path+'/decision', headers=owner, json={'status': 'confirmed'}).status_code == 200
    assert client.get('/api/listings').json() == []
    assert client.get('/api/my/deals', headers=wagon).json()[0]['track_email'] == 'track@example.com'
    assert client.post(path+'/complete', headers=other_owner).status_code == 403
    assert client.post(path+'/complete', headers=owner).json() == {'status': 'completed'}
    assert client.post(path+'/complete', headers=owner).status_code == 409
    assert client.get('/api/listings').json()[0]['free_capacity'] == 1
    assert client.get('/api/my/deals', headers=wagon).json()[0]['status'] == 'completed'
    assert {n['event'] for n in client.get('/api/my/notifications', headers=wagon).json()} == {'confirmed', 'completed'}
    for event in ('confirmed', 'completed'):
        assert client.post('/api/my/notifications/seen', headers=wagon,
                           json=[{'deal_id': deal, 'event': event}]).status_code == 204
        if event == 'confirmed':
            assert client.get('/api/my/notifications', headers=wagon).json() == [
                {'deal_id': deal, 'event': 'completed'}]
    assert client.get('/api/my/notifications', headers=wagon).json() == []
    assert client.get('/healthz').status_code == 200


def test_catalog_cursor_and_unicode_city(client):
    track = register(client, 'track', 'track@example.com')
    first = listing(client, track).json()['id']
    second = listing(client, track).json()['id']
    listing(client, track, city='Тверь')
    assert [x['id'] for x in client.get('/api/listings?city=МОСКВА&limit=1').json()] == [second]
    assert [x['id'] for x in client.get(f'/api/listings?city=МОСКВА&limit=1&before={second}').json()] == [first]


def test_master_creates_revokes_and_reissues_invites(client, monkeypatch):
    monkeypatch.setenv('PILOT_ADMIN_EMAIL', 'master@example.com')
    monkeypatch.setenv('PILOT_INVITES_JSON',
                       '{"master@example.com":{"role":"track","code":"MMMMMMMMMMMMMMMMMMMMMMMMMMMM"}}')
    assert client.get('/api/admin/invites').status_code == 401
    member = register(client, 'track', 'ordinary@example.com')
    master_fields = {'name': 'Администратор', 'email': 'master@example.com', 'role': 'track',
                     'password': 'securepassword'}
    assert client.post('/api/register', json=master_fields).status_code == 403
    root = client.post('/api/register', json={**master_fields, 'invite_code': 'M' * 28})
    assert root.status_code == 201 and root.json()['user']['is_admin'] is True
    master = session_header(root)
    assert client.get('/api/admin/invites', headers=member).status_code == 403
    fields = {'email': 'testwagon@example.com', 'role': 'wagon', 'inn': '1234567890'}
    assert client.post('/api/admin/invites', headers=member, json=fields).status_code == 403
    assert client.post('/api/admin/invites', headers={**master, 'X-WagonHub-Request': ''},
                       json=fields).status_code == 403
    assert client.post('/api/admin/invites', headers=master,
                       json={**fields, 'inn': None}).status_code == 422
    assert client.post('/api/admin/invites', headers=master,
                       json={'email': master_fields['email'], 'role': 'track'}).status_code == 409
    r = client.post('/api/admin/invites', headers=master, json=fields)
    assert r.status_code == 201 and r.headers['cache-control'] == 'no-store'
    first = r.json()
    assert client.post('/api/admin/invites', headers=master, json=fields).status_code == 409
    rows = client.get('/api/admin/invites', headers=master)
    assert rows.json()[0]['status'] == 'pending' and first['token'] not in rows.text
    assert client.post(f"/api/admin/invites/{first['id']}/revoke", headers=master).status_code == 204
    assert client.post(f"/api/admin/invites/{first['id']}/revoke", headers=master).status_code == 409

    monkeypatch.setattr(security, 'production', lambda: True)
    signup = {'email': fields['email'], 'role': 'wagon', 'name': 'Участник пилота',
              'password': 'securepassword'}
    assert client.post('/api/register', json={**signup, 'invite_token': first['token']}).status_code == 403
    second = client.post('/api/admin/invites', headers=master, json=fields).json()
    assert first['token'] != second['token']
    signup['invite_token'] = second['token']
    assert client.post('/api/register', json={**signup, 'email': 'other@example.com'}).status_code == 403
    assert client.post('/api/register', json={**signup, 'role': 'track'}).status_code == 403
    signed = client.post('/api/register', json=signup)
    assert signed.status_code == 201, signed.text
    assert signed.json()['user']['is_admin'] is False
    assert client.post('/api/register', json=signup).status_code == 403
    assert company(client, session_header(signed), inn='1234567891').status_code == 403
    assert company(client, session_header(signed), inn=fields['inn']).status_code == 201
    relogin = client.post('/api/login', json={'email': signup['email'], 'password': signup['password']})
    assert relogin.status_code == 200
    rows = client.get('/api/admin/invites', headers=master).json()
    assert {i['id']:i['status'] for i in rows} == {first['id']:'revoked',second['id']:'used'}
    assert client.post(f"/api/admin/invites/{second['id']}/revoke", headers=master).status_code == 409


def test_expired_links_and_private_pages(client, monkeypatch):
    from wagonhub import invites

    monkeypatch.setenv('YANDEX_METRIKA_ID', '12345678')
    for path in ('/invite', '/admin'):
        response = client.get(path)
        assert response.status_code == 200 and 'window.WAGONHUB_METRIKA_ID=null' in response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'window.WAGONHUB_METRIKA_ID=12345678' in client.get('/').text

    admin = register(client, 'track', 'existing@example.com')
    monkeypatch.setenv('PILOT_ADMIN_EMAIL', 'existing@example.com')
    monkeypatch.setattr(invites.time, 'time', lambda: 10000)
    data = {'email': 'new@example.com', 'role': 'track'}
    first = client.post('/api/admin/invites', json=data, headers=admin).json()
    assert first['expires_at'] == 10000 + 7 * 86400
    monkeypatch.setattr(invites.time, 'time', lambda: first['expires_at'])
    assert client.get('/api/admin/invites', headers=admin).json()[0]['status'] == 'expired'
    assert client.post('/api/register', json={**data, 'name': 'Участник',
                       'password': 'securepassword', 'invite_token': first['token']}).status_code == 403
    assert client.post('/api/admin/invites', json=data, headers=admin).status_code == 201


def test_multiple_admins_and_revoking_one_without_removing_account(client, monkeypatch):
    first = register(client, 'track', 'first@example.com')
    legacy = register(client, 'track', 'legacy@example.com')
    ordinary = register(client, 'track', 'member@example.com')
    monkeypatch.setenv('PILOT_ADMIN_EMAIL', 'legacy@example.com')
    monkeypatch.setenv('PILOT_ADMIN_EMAILS', ' First@example.com, , SECOND@example.com ')
    monkeypatch.setenv('PILOT_INVITES_JSON', '{"first@example.com":{"role":"track","code":"AAAAAAAAAAAAAAAAAAAAAAAAAAAA"},'
                       '"second@example.com":{"role":"track","code":"BBBBBBBBBBBBBBBBBBBBBBBBBBBB"},'
                       '"legacy@example.com":{"role":"track","code":"CCCCCCCCCCCCCCCCCCCCCCCCCCCC"},'
                       '"member@example.com":{"role":"track","code":"DDDDDDDDDDDDDDDDDDDDDDDDDDDD"}}')
    monkeypatch.setattr(security, 'production', lambda: True)
    second_payload = {'name':'Второй администратор', 'email':'second@example.com',
                      'role':'track', 'password':'securepassword'}
    assert client.post('/api/register', json=second_payload).status_code == 403
    second = client.post('/api/register', json={**second_payload, 'invite_code':'B'*28})
    assert second.status_code == 201 and second.json()['user']['is_admin'] is True
    second_headers = session_header(second)
    assert client.get('/api/admin/invites', headers=first).status_code == 200
    assert client.post('/api/admin/invites', headers=second_headers,
                       json={'email':'participant@example.com','role':'track'}).status_code == 201
    assert client.get('/api/admin/invites', headers=legacy).status_code == 403
    assert client.get('/api/admin/invites', headers=ordinary).status_code == 403
    monkeypatch.setenv('PILOT_ADMIN_EMAILS', 'first@example.com')
    assert client.get('/api/admin/invites', headers=second_headers).status_code == 403
    assert client.get('/api/me', headers=second_headers).json()['is_admin'] is False
    assert client.get('/api/admin/invites', headers=first).status_code == 200
