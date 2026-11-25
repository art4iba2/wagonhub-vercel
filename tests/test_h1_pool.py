"""Exercise the actual Psycopg pool; only the network dial is substituted here."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus as Status
from psycopg_pool import PoolTimeout

from wagonhub import db
from wagonhub.app import app


class TestSocketDouble:
    __test__ = False
    autocommit = True

    def __init__(self):
        self.pgconn = SimpleNamespace(transaction_status=Status.IDLE)
        self.statements = []

    def execute(self, stmt, args=()):
        if self.pgconn.transaction_status == Status.UNKNOWN:
            raise db.psycopg.OperationalError('disconnected')
        self.statements.append(stmt)
        if stmt == 'BEGIN':
            self.pgconn.transaction_status = Status.INTRANS
        elif stmt in ('COMMIT', 'ROLLBACK'):
            self.pgconn.transaction_status = Status.IDLE
        return self

    def close(self):
        self.pgconn.transaction_status = Status.UNKNOWN


@pytest.fixture
def sockets(monkeypatch):
    db.close_pool()
    monkeypatch.setattr(db, 'POOL_ENABLED', True)
    monkeypatch.setattr(db, 'POOL_SIZE', 1)
    monkeypatch.setattr(db, 'DATABASE_URL', 'postgresql://example.invalid/test')
    opened = []

    def dial(cls, dsn, **opts):
        c = TestSocketDouble()
        opened.append(c)
        return c

    monkeypatch.setattr(db.psycopg.Connection, 'connect', classmethod(dial))
    yield opened
    db.close_pool()


def test_reuse_rollback_and_stale_socket(sockets):
    with pytest.raises(ValueError), db.transaction() as c:
        first = c.raw
        raise ValueError('cancel')
    assert first.statements[-1] == 'ROLLBACK'
    with closing(db.connect()) as c:
        assert c.raw is first
        with pytest.raises(PoolTimeout):
            db._get_pool().getconn(timeout=0.05)
    first.close()  # idle socket disappeared between requests
    with closing(db.connect()) as c:
        assert c.raw is not first
    assert len(sockets) == 2


def test_threads_share_one_bounded_pool(sockets):
    def reader(_):
        with closing(db.connect()) as c:
            c.execute('SELECT 1')
            return id(c.raw)
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert len(set(workers.map(reader, range(24)))) == 1
    assert len(sockets) == 1
    db.close_pool()
    assert sockets[0].pgconn.transaction_status == Status.UNKNOWN


def test_mode_header_and_busy_response(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_URL', '')
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.sqlite'))
    with TestClient(app) as client:
        res = client.get('/api/listings')
        assert (res.headers['x-wagonhub-db-mode'], res.headers['cache-control']) == ('sqlite', 'no-store')
        def exhausted():
            raise PoolTimeout('busy')
        monkeypatch.setattr(db, 'connect', exhausted)
        res = client.get('/api/listings')
        assert res.status_code == 503 and res.headers['retry-after'] == '1'
