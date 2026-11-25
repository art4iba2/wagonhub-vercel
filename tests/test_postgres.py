"""Isolated acceptance test, enabled by compose.check.yaml."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_app import register, listing, company
from wagonhub import db
from wagonhub.app import app


def test_postgres_last_place_and_restart(monkeypatch, request):
    _last_place(monkeypatch, request, False)


def test_postgres_last_place_and_restart_pooled(monkeypatch, request):
    _last_place(monkeypatch, request, True)


def _last_place(monkeypatch, request, pool_enabled):
    url = os.environ.get('WAGONHUB_TEST_POSTGRES')
    if not url:
        pytest.skip('Requires isolated PostgreSQL from compose.check.yaml')
    db.close_pool()
    request.addfinalizer(db.close_pool)
    monkeypatch.setattr(db, 'POOL_ENABLED', pool_enabled)
    monkeypatch.setattr(db, 'DATABASE_URL', url)
    nonce = uuid4().hex
    headers = {'X-WagonHub-Request': '1'}
    with TestClient(app, headers=headers) as client:
        owner = register(client, 'track', f'track-{nonce}@example.com')
        buyers = [register(client, 'wagon', f'wagon-{i}-{nonce}@example.com') for i in range(2)]
        for buyer in buyers:
            inn = str(uuid4().int % 10**10).zfill(10)
            assert company(client, buyer, inn).status_code == 201
        item = listing(client, owner, capacity=1).json()['id']
        catalogue = client.get('/api/listings?city=МОСКВА')
        assert catalogue.headers['X-WagonHub-DB-Mode'] == ('pool' if pool_enabled else 'direct')
        assert item in [x['id'] for x in catalogue.json()]
        deals = []
        for buyer in buyers:
            result = client.post('/api/deals', headers=buyer, json={'listing_id': item, 'wagons': 1})
            assert result.status_code == 201, result.text
            deals.append(result.json()['id'])
        ready = Barrier(2)

        def decide(deal_id):
            with TestClient(app, headers={**headers, **owner}) as contender:
                ready.wait(timeout=10)
                return contender.post(f'/api/deals/{deal_id}/decision', json={'status': 'confirmed'})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, deals))
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        confirmed = deals[next(i for i, r in enumerate(results) if r.status_code == 200)]
        assert item not in [x['id'] for x in client.get('/api/listings').json()]
    with TestClient(app, headers=headers) as restarted:
        assert restarted.get('/api/me', headers=owner).status_code == 200
        result = restarted.post(f'/api/deals/{confirmed}/complete', headers=owner)
        assert result.status_code == 200, result.text
        assert item in [x['id'] for x in restarted.get('/api/listings').json()]


def test_pool_reuses_pg_session_after_rollback(monkeypatch, request):
    """Use the test PostgreSQL directly (no external PgBouncer in this fixture)."""
    url = os.environ.get('WAGONHUB_TEST_POSTGRES')
    if not url:
        pytest.skip('Requires isolated PostgreSQL from compose.check.yaml')
    db.close_pool()
    request.addfinalizer(db.close_pool)
    monkeypatch.setattr(db, 'DATABASE_URL', url)
    monkeypatch.setattr(db, 'POOL_ENABLED', True)
    monkeypatch.setattr(db, 'POOL_SIZE', 1)
    with pytest.raises(db.psycopg.errors.DivisionByZero):
        with db.transaction() as con:
            pid = con.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            con.execute('SELECT 1 / 0')
    with db.transaction() as con:
        assert con.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'] == pid
