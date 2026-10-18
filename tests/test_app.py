import pytest
from fastapi.testclient import TestClient
from wagonhub import db
from wagonhub.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    with TestClient(app) as test:
        yield test


def register(client, role, email):
    response = client.post('/api/register', json={'name': 'Тестовый пользователь', 'email': email,
                                                   'password': 'securepassword', 'role': role})
    assert response.status_code == 201, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


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
    assert client.get('/api/me', headers={'Authorization': 'Bearer invalid'}).status_code == 401
    assert client.get('/api/me', headers={'Authorization': 'Basic abc'}).status_code == 401
    assert client.post('/api/logout', headers=track).status_code == 204
    assert client.get('/api/me', headers=track).status_code == 401
    assert client.get('/api/me', headers={'Authorization': 'Bearer '+login.json()['token']}).status_code == 200


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


def test_vercel_requires_persistent_database(monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_URL', '')
    monkeypatch.setenv('VERCEL', '1')
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
