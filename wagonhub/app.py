"""FastAPI API and single-page frontend for wagon-space rental."""
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import PoolTimeout, TooManyRequests

from . import db, invites, password_reset, security
from .schemas import AdminInviteIn, CompanyIn, DealIn, Decision, ListingIn, Login, NotificationReceipt, Register, ResetComplete, ResetRequest, ResetVerify

STATIC = Path(__file__).parent / 'static'
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application):
    if security.production():
        security.invitations()
    db.init_db()
    yield


app = FastAPI(title='ВагонМесто API', version='0.1.0', lifespan=lifespan,
              docs_url=None if security.production() else '/docs',
              redoc_url=None if security.production() else '/redoc',
              openapi_url=None if security.production() else '/openapi.json')
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.exception_handler(PoolTimeout)
@app.exception_handler(TooManyRequests)
async def pool_busy(request: Request, exc: Exception):
    return JSONResponse({'detail': 'Сервис временно занят. Повторите запрос позже.'},
                        status_code=503, headers={'Cache-Control': 'no-store', 'Retry-After': '1'})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith('/api/password-reset/'):
        messages = {
            'email': 'Проверьте адрес электронной почты',
            'code': 'Введите 8 цифр из письма',
            'token': 'Начните восстановление заново',
            'password': 'Пароль должен содержать от 8 до 128 символов',
        }
        errors = [{'msg': messages.get(error['loc'][-1], 'Проверьте данные формы')}
                  for error in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=422, headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


def fail(code, message):
    raise HTTPException(status_code=code, detail=message)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()


def public_user(row):
    return {**{key: row[key] for key in ('id', 'name', 'email', 'role')}, 'is_admin': security.is_admin(row)}


def issue_token(con, user_id):
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    con.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)', (digest, user_id, expiry))
    return token


def current_user(request: Request):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        require_browser_header(request)
    token = request.cookies.get(security.COOKIE)
    if not token:
        fail(401, 'Требуется вход в аккаунт')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(db.connect()) as con:
        row = con.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                             WHERE s.token_hash=? AND s.expires_at>?''',
                          (digest, datetime.now(timezone.utc).isoformat())).fetchone()
        if not row or not pilot_member(con, row):
            fail(401, 'Сессия недействительна. Войдите снова')
    return dict(row)


def pilot_member(con, user):
    return not security.production() or invites.membership(con, user) is not None


def require_admin(user=Depends(current_user)):
    if not security.is_admin(user):
        fail(403, 'Раздел доступен только администратору')
    return user


def require_browser_header(request: Request):
    # Browsers cannot send this header cross-origin without a successful CORS preflight.
    if request.headers.get('X-WagonHub-Request') != '1':
        fail(403, 'Обновите страницу и повторите действие')


def set_session(response, token):
    response.set_cookie(security.COOKIE, token, httponly=True, secure=security.production(),
                        samesite='lax', max_age=7 * 86400)
    response.headers['Cache-Control'] = 'no-store'


def require_role(user, role):
    if user['role'] != role:
        fail(403, 'Действие недоступно для этого типа аккаунта')


def listing_dict(row):
    data = dict(row)
    data['price'] = data.pop('price_kopecks') / 100
    data['free_capacity'] = data['capacity'] - data.pop('reserved')
    return data


def page(*, private_page=False):
    raw_id = os.environ.get('YANDEX_METRIKA_ID', '').strip()
    counter_id = 'null'
    if raw_id and not private_page:
        if raw_id.isascii() and raw_id.isdecimal() and len(raw_id) <= 12 and int(raw_id) > 0:
            counter_id = str(int(raw_id))
        else:
            LOGGER.warning('Некорректный YANDEX_METRIKA_ID: аналитика отключена')
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    html = html.replace('__YANDEX_METRIKA_ID__', counter_id).replace(
        '__PILOT_INVITE_REQUIRED__', str(security.production()).lower()).replace(
        '__PILOT_ADMIN_ENABLED__', str(bool(security.admin_emails())).lower())
    html = html.replace('__PASSWORD_RESET_ENABLED__', str(password_reset.settings() is not None).lower())
    headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'} if private_page else None
    return HTMLResponse(html, headers=headers)


@app.get('/')
def index():
    return page()


@app.get('/invite', include_in_schema=False)
def invite_page():
    # No analytics scripts can initialize on the invitation landing page.
    return page(private_page=True)


@app.get('/admin', include_in_schema=False)
def admin_page():
    return page(private_page=True)


@app.get('/reset-password', include_in_schema=False)
def reset_page():
    return page(private_page=True)


@app.post('/api/register', status_code=201)
def register(payload: Register, request: Request, response: Response):
    require_browser_header(request)
    email = str(payload.email).lower()
    security.throttle('register', request, email)
    admin_signup = email in security.admin_emails()
    if (admin_signup or (security.production() and payload.invite_token is None)) and not (
            security.allowed_signup(email, payload.role, payload.invite_code)):
        fail(403, 'Проверьте email, роль и код приглашения')
    salt = secrets.token_hex(16)
    digest = password_hash(payload.password, salt)
    try:
        with db.transaction() as con:
            claim = None
            if payload.invite_token is not None:
                claim = invites.claim(con, email, payload.role, payload.invite_token)
                if claim is None:
                    fail(403, 'Ссылка недействительна, истекла или не подходит для этого email и роли')
            user_id = db.insert_id(con, 'INSERT INTO users(name,email,role,salt,password_hash) VALUES (?,?,?,?,?)',
                                   (payload.name, email, payload.role, salt, digest))
            if claim:
                invites.assign(con, claim['id'], user_id)
            token = issue_token(con, user_id)
    except db.INTEGRITY_ERRORS:
        fail(409, 'Этот email уже зарегистрирован')
    set_session(response, token)
    return {'user': public_user({'id': user_id, 'name': payload.name, 'email': email, 'role': payload.role})}


@app.post('/api/login')
def login(payload: Login, request: Request, response: Response):
    require_browser_header(request)
    email = str(payload.email).lower()
    security.throttle('login', request, email)
    with closing(db.connect()) as con:
        row = con.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        member = row is not None and pilot_member(con, row)
    digest = password_hash(payload.password, row['salt'] if row else '0' * 32)
    if not row or not hmac.compare_digest(row['password_hash'], digest) or not member:
        fail(401, 'Неверный email или пароль')
    with db.transaction() as con:
        # Serialize session issuance with password replacement on the same account.
        fresh = reset_user(con, email, lock=True)
        if not fresh or fresh['password_hash'] != row['password_hash'] or fresh['salt'] != row['salt']:
            fail(401, 'Неверный email или пароль')
        token = issue_token(con, fresh['id'])
    set_session(response, token)
    return {'user': public_user(row)}


def reset_user(con, email, *, lock=False):
    query = 'SELECT id,email,role,password_hash,salt FROM users WHERE email=?'
    if lock and db.DATABASE_URL:
        query += ' FOR UPDATE'
    return con.execute(query, (email,)).fetchone()


def reset_context(action, request, response, address, *, email_limit):
    require_browser_header(request)
    config = password_reset.settings()
    if not config:
        fail(503, 'Восстановление пароля пока недоступно')
    email = str(address).lower()
    security.throttle(action, request, email, email_limit=email_limit)
    response.headers['Cache-Control'] = 'no-store'
    return email, config


@app.post('/api/password-reset/request', status_code=202)
def request_reset(payload: ResetRequest, request: Request, response: Response):
    started = time.monotonic()
    email, (username, password, sender, secret) = reset_context('reset-send', request, response, payload.email, email_limit=3)
    with db.transaction() as con:
        row = reset_user(con, email, lock=True)
        issued = password_reset.issue(con, row['id'], secret) if row and pilot_member(con, row) else None
    if issued:
        challenge, code = issued
        # Vercel can pause work after the response, so finish SMTP first.
        password_reset.deliver(username, password, sender, row['id'], email, challenge, code)
    password_reset.pad_request_time(started)
    return {'message': 'Если аккаунт доступен, мы отправим код на указанный email.'}


@app.post('/api/password-reset/verify')
def verify_reset(payload: ResetVerify, request: Request, response: Response):
    email, (_, _, _, secret) = reset_context('reset-verify', request, response, payload.email, email_limit=10)
    with db.transaction() as con:
        row = reset_user(con, email, lock=True)
        token = password_reset.verify(con, row['id'], secret, payload.code) if row and pilot_member(con, row) else None
    if not token:
        fail(400, 'Код неверен или истёк. При необходимости запросите новый.')
    return {'token': token}


@app.post('/api/password-reset/complete', status_code=204)
def complete_reset(payload: ResetComplete, request: Request, response: Response):
    email, (_, _, _, secret) = reset_context('reset-complete', request, response, payload.email, email_limit=5)
    salt = secrets.token_hex(16)
    digest = password_hash(payload.password, salt)
    with db.transaction() as con:
        row = reset_user(con, email, lock=True)
        if not row or not pilot_member(con, row) or not password_reset.consume(con, row['id'], secret, payload.token):
            fail(400, 'Подтверждение недействительно или истекло. Начните восстановление заново.')
        con.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?', (salt, digest, row['id']))
        con.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
    # Clear any existing site session in this browser; require a regular login.
    response.delete_cookie(security.COOKIE, secure=security.production(), httponly=True, samesite='lax')


@app.post('/api/logout', status_code=204)
def logout(request: Request, response: Response, user=Depends(current_user)):
    with db.transaction() as con:
        con.execute('DELETE FROM sessions WHERE token_hash=?',
                    (hashlib.sha256(request.cookies[security.COOKIE].encode()).hexdigest(),))
    response.delete_cookie(security.COOKIE, secure=security.production(), httponly=True, samesite='lax')


@app.get('/api/me')
def me(user=Depends(current_user)):
    return public_user(user)


@app.post('/api/company', status_code=201)
def create_company(payload: CompanyIn, user=Depends(current_user)):
    require_role(user, 'wagon')
    try:
        with db.transaction() as con:
            invite = invites.membership(con, user)
            if (security.production() or invite is not None) and (
                    invite is None or str(invite.get('inn')) != payload.inn):
                fail(403, 'Используйте ИНН компании, согласованный для пилота')
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


RESERVED = """COALESCE((SELECT SUM(d.wagons) FROM deals d
 WHERE d.listing_id=l.id AND d.status='confirmed'
 AND NOT EXISTS (SELECT 1 FROM deal_releases r WHERE r.deal_id=d.id)),0)"""
LISTING_QUERY = f"""SELECT l.*,u.name AS owner_name, {RESERVED} AS reserved
 FROM listings l JOIN users u ON u.id=l.owner_id"""


@app.get('/api/listings')
def listings(response: Response, city: str | None = Query(default=None, max_length=100),
             min_capacity: int = Query(default=1, ge=1),
             max_price: float | None = Query(default=None, gt=0, le=9999999999),
             before: int | None = Query(default=None, gt=0, le=9223372036854775807),
             limit: int = Query(default=50, ge=1, le=100)):
    clauses = ["l.status='available'"]
    args = []
    if max_price is not None:
        clauses.append('l.price_kopecks <= ?')
        args.append(int(max_price * 100))
    if city and city.strip():
        lower = 'LOWER' if db.DATABASE_URL else 'unicode_lower'
        locate = 'STRPOS' if db.DATABASE_URL else 'INSTR'
        clauses.append(f'{locate}({lower}(l.city), ?) > 0')
        args.append(city.strip().lower())
    if before is not None:
        clauses.append('l.id < ?')
        args.append(before)
    query = LISTING_QUERY + ' WHERE ' + ' AND '.join(clauses)
    query = f'SELECT * FROM ({query}) AS q WHERE capacity-reserved >= ? ORDER BY id DESC LIMIT ?'
    with closing(db.connect()) as con:
        rows = con.execute(query, [*args, min_capacity, limit]).fetchall()
    response.headers['X-WagonHub-DB-Mode'] = db.connection_mode()
    response.headers['Cache-Control'] = 'no-store'
    return [listing_dict(row) for row in rows]


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
        con.execute("""INSERT INTO deal_notifications(user_id,deal_id,event)
            SELECT wagon_user_id,id,'rejected' FROM deals WHERE listing_id=? AND status='pending'""", (listing_id,))
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
        notify(con, row['owner_id'], deal_id, 'pending')
    return {'id': deal_id, 'status': 'pending'}


DEALS_QUERY = '''SELECT d.id,d.listing_id,d.wagons,d.price_kopecks,
 CASE WHEN r.deal_id IS NULL THEN d.status ELSE 'completed' END AS status,d.created_at,d.decided_at,
 l.title,l.city,l.owner_id,c.name AS company_name,c.inn AS company_inn,
 c.email AS company_email,c.phone AS company_phone,u.name AS wagon_name,u.email AS wagon_email,
 CASE WHEN d.status='confirmed' THEN owner.email END AS track_email
 FROM deals d JOIN listings l ON l.id=d.listing_id JOIN companies c ON c.id=d.company_id
 JOIN users u ON u.id=d.wagon_user_id JOIN users owner ON owner.id=l.owner_id
 LEFT JOIN deal_releases r ON r.deal_id=d.id'''


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
        row = con.execute(f'''SELECT d.*,l.owner_id,l.capacity,l.status AS listing_status,
            {RESERVED} AS reserved
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
        notify(con, row['wagon_user_id'], deal_id, payload.status)
    return {'status': payload.status}


def notify(con, user_id, deal_id, event):
    con.execute('INSERT INTO deal_notifications(user_id,deal_id,event) VALUES (?,?,?)',
                (user_id, deal_id, event))


@app.post('/api/deals/{deal_id}/complete')
def complete(deal_id: int, user=Depends(current_user)):
    require_role(user, 'track')
    with db.transaction() as con:
        target = con.execute('SELECT listing_id FROM deals WHERE id=?', (deal_id,)).fetchone()
        if not target:
            fail(404, 'Заявка не найдена')
        db.lock_listing(con, target['listing_id'])
        row = con.execute("""SELECT l.owner_id,d.wagon_user_id,d.status,r.deal_id AS released
            FROM deals d JOIN listings l ON l.id=d.listing_id
            LEFT JOIN deal_releases r ON r.deal_id=d.id WHERE d.id=?""", (deal_id,)).fetchone()
        if row['owner_id'] != user['id']:
            fail(403, 'Это чужая заявка')
        if row['status'] != 'confirmed' or row['released'] is not None:
            fail(409, 'Завершить можно только активное размещение')
        con.execute('INSERT INTO deal_releases(deal_id,released_at) VALUES (?,?)',
                    (deal_id, datetime.now(timezone.utc).isoformat()))
        notify(con, row['wagon_user_id'], deal_id, 'completed')
    return {'status': 'completed'}


@app.get('/api/my/notifications')
def notifications(user=Depends(current_user)):
    with closing(db.connect()) as con:
        rows = con.execute("""SELECT deal_id,event FROM deal_notifications
            WHERE user_id=? AND seen=0 ORDER BY deal_id DESC LIMIT 100""",
                           (user['id'],)).fetchall()
    return [dict(row) for row in rows]


@app.post('/api/my/notifications/seen', status_code=204)
def mark_seen(receipts: list[NotificationReceipt] = Body(max_length=100), user=Depends(current_user)):
    with db.transaction() as con:
        for receipt in receipts:
            con.execute('UPDATE deal_notifications SET seen=1 WHERE user_id=? AND deal_id=? AND event=?',
                        (user['id'], receipt.deal_id, receipt.event))


@app.get('/healthz')
def health():
    try:
        with closing(db.connect()) as con:
            con.execute('SELECT id FROM listings LIMIT 1').fetchone()
    except (db.psycopg.Error, db.sqlite3.Error):
        fail(503, 'База данных недоступна')
    return {'status': 'ok'}


@app.get('/api/admin/invites')
def admin_invites(response: Response, user=Depends(require_admin)):
    response.headers['Cache-Control'] = 'no-store'
    with closing(db.connect()) as con:
        return invites.recent(con)


@app.post('/api/admin/invites', status_code=201)
def admin_create_invite(payload: AdminInviteIn, response: Response, user=Depends(require_admin)):
    email = str(payload.email).lower()
    if email in security.admin_emails() or email in security.invitations():
        fail(409, 'Этот адрес уже настроен в списке приглашённых')
    with db.transaction() as con:
        invite = invites.issue(con, user['id'], email, payload.role, payload.inn)
    if not invite:
        fail(409, 'Аккаунт или действующее приглашение для этого email уже есть')
    response.headers['Cache-Control'] = 'no-store'
    return {**invite, 'role': payload.role}


@app.post('/api/admin/invites/{invite_id}/revoke', status_code=204)
def admin_revoke_invite(invite_id: str, user=Depends(require_admin)):
    with db.transaction() as con:
        if not invites.revoke(con, invite_id):
            fail(409, 'Можно отозвать только действующее неиспользованное приглашение')
