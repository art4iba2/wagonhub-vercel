"""FastAPI API and single-page frontend for wagon-space rental."""
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .schemas import CompanyIn, DealIn, Decision, ListingIn, Login, Register

STATIC = Path(__file__).parent / 'static'


@asynccontextmanager
async def lifespan(application):
    db.init_db()
    yield


app = FastAPI(title='ВагонМесто API', version='0.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


def fail(code, message):
    raise HTTPException(status_code=code, detail=message)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()


def public_user(row):
    return {key: row[key] for key in ('id', 'name', 'email', 'role')}


def issue_token(con, user_id):
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    con.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)', (digest, user_id, expiry))
    return token


def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        fail(401, 'Требуется вход в аккаунт')
    token = authorization[7:]
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(db.connect()) as con:
        row = con.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                             WHERE s.token_hash=? AND s.expires_at>?''',
                          (digest, datetime.now(timezone.utc).isoformat())).fetchone()
    if not row:
        fail(401, 'Сессия истекла. Войдите снова')
    return dict(row)


def require_role(user, role):
    if user['role'] != role:
        fail(403, 'Действие недоступно для этого типа аккаунта')


def listing_dict(row):
    data = dict(row)
    data['price'] = data.pop('price_kopecks') / 100
    data['free_capacity'] = data['capacity'] - data.pop('reserved')
    return data


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')


@app.post('/api/register', status_code=201)
def register(payload: Register):
    salt = secrets.token_hex(16)
    try:
        with db.transaction() as con:
            user_id = db.insert_id(con, 'INSERT INTO users(name,email,role,salt,password_hash) VALUES (?,?,?,?,?)',
                                   (payload.name, str(payload.email).lower(), payload.role, salt,
                                    password_hash(payload.password, salt)))
            token = issue_token(con, user_id)
    except db.INTEGRITY_ERRORS:
        fail(409, 'Этот email уже зарегистрирован')
    return {'token': token, 'user': {'id': user_id, 'name': payload.name,
                                     'email': str(payload.email).lower(), 'role': payload.role}}


@app.post('/api/login')
def login(payload: Login):
    with db.transaction() as con:
        row = con.execute('SELECT * FROM users WHERE email=?', (str(payload.email).lower(),)).fetchone()
        if not row or not hmac.compare_digest(row['password_hash'], password_hash(payload.password, row['salt'])):
            fail(401, 'Неверный email или пароль')
        token = issue_token(con, row['id'])
    return {'token': token, 'user': public_user(row)}


@app.post('/api/logout', status_code=204)
def logout(authorization: str | None = Header(default=None), user=Depends(current_user)):
    with db.transaction() as con:
        con.execute('DELETE FROM sessions WHERE token_hash=?',
                    (hashlib.sha256(authorization[7:].encode()).hexdigest(),))


@app.get('/api/me')
def me(user=Depends(current_user)):
    return public_user(user)


@app.post('/api/company', status_code=201)
def create_company(payload: CompanyIn, user=Depends(current_user)):
    require_role(user, 'wagon')
    try:
        with db.transaction() as con:
            company_id = db.insert_id(con, 'INSERT INTO companies(user_id,name,inn,email,phone) VALUES (?,?,?,?,?)',
                                      (user['id'], payload.name, payload.inn, str(payload.email), payload.phone.strip()))
    except db.INTEGRITY_ERRORS:
        fail(409, 'Компания уже создана или ИНН занят')
    return {'id': company_id, **payload.model_dump(mode='json')}


@app.get('/api/company')
def company(user=Depends(current_user)):
    require_role(user, 'wagon')
    with closing(db.connect()) as con:
        row = con.execute('SELECT id,name,inn,email,phone FROM companies WHERE user_id=?', (user['id'],)).fetchone()
    return dict(row) if row else None


@app.post('/api/listings', status_code=201)
def create_listing(payload: ListingIn, user=Depends(current_user)):
    require_role(user, 'track')
    price_kopecks = int(payload.price * 100)
    with db.transaction() as con:
        listing_id = db.insert_id(con, '''INSERT INTO listings(owner_id,title,city,address,capacity,price_kopecks,description)
                                          VALUES (?,?,?,?,?,?,?)''',
                                  (user['id'], payload.title, payload.city, payload.address,
                                   payload.capacity, price_kopecks, payload.description.strip()))
    return {'id': listing_id, 'status': 'available'}


LISTING_QUERY = '''SELECT l.*,u.name AS owner_name,
  COALESCE((SELECT SUM(wagons) FROM deals WHERE listing_id=l.id AND status='confirmed'),0) AS reserved
  FROM listings l JOIN users u ON u.id=l.owner_id'''


@app.get('/api/listings')
def listings(city: str | None = Query(default=None, max_length=100),
             min_capacity: int = Query(default=1, ge=1),
             max_price: float | None = Query(default=None, gt=0)):
    clauses = ["l.status='available'", "l.capacity - COALESCE((SELECT SUM(wagons) FROM deals WHERE listing_id=l.id AND status='confirmed'),0) >= ?"]
    args = [min_capacity]
    if max_price is not None:
        clauses.append('l.price_kopecks <= ?')
        args.append(int(max_price * 100))
    query = LISTING_QUERY + ' WHERE ' + ' AND '.join(clauses) + ' ORDER BY l.created_at DESC,l.id DESC'
    with closing(db.connect()) as con:
        rows = con.execute(query, args).fetchall()
    # SQLite LIKE is case insensitive for ASCII only. casefold handles Cyrillic cities.
    if city and city.strip():
        rows = [row for row in rows if city.strip().casefold() in row['city'].casefold()]
    return [listing_dict(row) for row in rows[:100]]


@app.get('/api/my/listings')
def my_listings(user=Depends(current_user)):
    require_role(user, 'track')
    with closing(db.connect()) as con:
        rows = con.execute(LISTING_QUERY + ' WHERE l.owner_id=? ORDER BY l.id DESC', (user['id'],)).fetchall()
    return [listing_dict(row) for row in rows]


@app.put('/api/listings/{listing_id}')
def update_listing(listing_id: int, payload: ListingIn, user=Depends(current_user)):
    require_role(user, 'track')
    with db.transaction() as con:
        db.lock_listing(con, listing_id)
        row = con.execute(LISTING_QUERY + ' WHERE l.id=?', (listing_id,)).fetchone()
        if not row:
            fail(404, 'Объявление не найдено')
        if row['owner_id'] != user['id']:
            fail(403, 'Это чужое объявление')
        if payload.capacity < row['reserved']:
            fail(409, 'Вместимость меньше числа мест в подтверждённых сделках')
        con.execute('''UPDATE listings SET title=?,city=?,address=?,capacity=?,price_kopecks=?,description=?
                       WHERE id=?''', (payload.title, payload.city, payload.address, payload.capacity,
                                      int(payload.price * 100), payload.description.strip(), listing_id))
    return {'id': listing_id, 'status': row['status']}


@app.post('/api/listings/{listing_id}/withdraw')
def withdraw(listing_id: int, user=Depends(current_user)):
    require_role(user, 'track')
    with db.transaction() as con:
        db.lock_listing(con, listing_id)
        row = con.execute('SELECT owner_id,status FROM listings WHERE id=?', (listing_id,)).fetchone()
        if not row:
            fail(404, 'Объявление не найдено')
        if row['owner_id'] != user['id']:
            fail(403, 'Это чужое объявление')
        if row['status'] == 'withdrawn':
            fail(409, 'Объявление уже снято')
        con.execute("UPDATE listings SET status='withdrawn' WHERE id=?", (listing_id,))
        con.execute("UPDATE deals SET status='rejected',decided_at=CURRENT_TIMESTAMP WHERE listing_id=? AND status='pending'", (listing_id,))
    return {'status': 'withdrawn'}


@app.post('/api/deals', status_code=201)
def request_deal(payload: DealIn, user=Depends(current_user)):
    require_role(user, 'wagon')
    with db.transaction() as con:
        company = con.execute('SELECT id FROM companies WHERE user_id=?', (user['id'],)).fetchone()
        if not company:
            fail(409, 'Сначала создайте компанию')
        db.lock_listing(con, payload.listing_id)
        row = con.execute(LISTING_QUERY + ' WHERE l.id=?', (payload.listing_id,)).fetchone()
        if not row or row['status'] != 'available':
            fail(404, 'Объявление недоступно')
        if payload.wagons > row['capacity'] - row['reserved']:
            fail(409, 'Недостаточно свободных мест')
        existing = con.execute("SELECT id FROM deals WHERE wagon_user_id=? AND listing_id=? AND status='pending'",
                               (user['id'], payload.listing_id)).fetchone()
        if existing:
            fail(409, 'Заявка на это объявление уже ожидает решения')
        deal_id = db.insert_id(con, '''INSERT INTO deals(listing_id,wagon_user_id,company_id,wagons,price_kopecks)
                                       VALUES (?,?,?,?,?)''',
                               (payload.listing_id, user['id'], company['id'], payload.wagons, row['price_kopecks']))
    return {'id': deal_id, 'status': 'pending'}


DEALS_QUERY = '''SELECT d.id,d.listing_id,d.wagons,d.price_kopecks,d.status,d.created_at,d.decided_at,
 l.title,l.city,l.owner_id,c.name AS company_name,c.inn AS company_inn,
 c.email AS company_email,c.phone AS company_phone,u.name AS wagon_name,u.email AS wagon_email
 FROM deals d JOIN listings l ON l.id=d.listing_id JOIN companies c ON c.id=d.company_id
 JOIN users u ON u.id=d.wagon_user_id'''


@app.get('/api/my/deals')
def my_deals(user=Depends(current_user)):
    clause = 'l.owner_id=?' if user['role'] == 'track' else 'd.wagon_user_id=?'
    with closing(db.connect()) as con:
        rows = con.execute(DEALS_QUERY + ' WHERE ' + clause + ' ORDER BY d.id DESC', (user['id'],)).fetchall()
    return [{**dict(row), 'price': row['price_kopecks'] / 100} for row in rows]


@app.post('/api/deals/{deal_id}/decision')
def decide(deal_id: int, payload: Decision, user=Depends(current_user)):
    require_role(user, 'track')
    with db.transaction() as con:
        target = con.execute('SELECT listing_id FROM deals WHERE id=?', (deal_id,)).fetchone()
        if not target:
            fail(404, 'Заявка не найдена')
        db.lock_listing(con, target['listing_id'])
        row = con.execute('''SELECT d.*,l.owner_id,l.capacity,l.status AS listing_status,
            COALESCE((SELECT SUM(wagons) FROM deals WHERE listing_id=d.listing_id AND status='confirmed'),0) AS reserved
            FROM deals d JOIN listings l ON l.id=d.listing_id WHERE d.id=?''', (deal_id,)).fetchone()
        if not row:
            fail(404, 'Заявка не найдена')
        if row['owner_id'] != user['id']:
            fail(403, 'Это чужая заявка')
        if row['status'] != 'pending':
            fail(409, 'Заявка уже обработана')
        if payload.status == 'confirmed' and (row['listing_status'] != 'available' or
                                               row['wagons'] > row['capacity'] - row['reserved']):
            fail(409, 'Недостаточно свободных мест или объявление снято')
        con.execute('UPDATE deals SET status=?,decided_at=CURRENT_TIMESTAMP WHERE id=?', (payload.status, deal_id))
    return {'status': payload.status}
