"""Email challenges and short-lived, single-use password reset grants."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib import error, request

from . import db

API = 'https://goapi.unisender.ru/ru/transactional/api/v1/email/send.json'
LOG = logging.getLogger(__name__)
TTL = 600
RESEND_AFTER = 60
MAX_ATTEMPTS = 5


def settings():
    key = os.getenv('UNISENDER_GO_API_KEY', '').strip()
    sender = os.getenv('RESET_FROM_EMAIL', '').strip()
    secret = os.getenv('PASSWORD_RESET_SECRET', '').strip()
    return (key, sender, secret) if key and sender and len(secret) >= 32 else None


def digest(secret, kind, uid, challenge, value):
    return hmac.new(secret.encode(), f'{kind}:{uid}:{challenge}:{value}'.encode(), hashlib.sha256).hexdigest()


def issue(con, uid, secret):
    now = int(time.time())
    challenge = secrets.token_hex(16)
    code = f'{secrets.randbelow(100_000_000):08d}'
    row = con.execute("""INSERT INTO password_resets(user_id,challenge,code_hash,expires_at,sent_at)
        VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        challenge=excluded.challenge, code_hash=excluded.code_hash, expires_at=excluded.expires_at,
        sent_at=excluded.sent_at, attempts=0, grant_hash=NULL, grant_expires_at=NULL
        WHERE password_resets.sent_at<=?
        AND (password_resets.grant_expires_at IS NULL OR password_resets.grant_expires_at<=?)
        RETURNING challenge""", (uid, challenge, digest(secret, 'code', uid, challenge, code),
                                  now+TTL, now, now-RESEND_AFTER, now)).fetchone()
    return (challenge, code) if row else None


def verify(con, uid, secret, code):
    now = int(time.time())
    row = con.execute("""UPDATE password_resets SET attempts=attempts+1
        WHERE user_id=? AND expires_at>? AND code_hash IS NOT NULL AND attempts<?
        RETURNING challenge, code_hash""", (uid, now, MAX_ATTEMPTS)).fetchone()
    if row is None or not hmac.compare_digest(
            row['code_hash'], digest(secret, 'code', uid, row['challenge'], code)):
        return None
    token = secrets.token_urlsafe(32)
    con.execute("""UPDATE password_resets SET code_hash=NULL, grant_hash=?, grant_expires_at=?
        WHERE user_id=?""", (digest(secret, 'grant', uid, row['challenge'], token), now+TTL, uid))
    return token


def consume(con, uid, secret, token):
    row = con.execute('SELECT challenge FROM password_resets WHERE user_id=?', (uid,)).fetchone()
    if not row:
        return False
    return con.execute("""DELETE FROM password_resets WHERE user_id=? AND grant_hash=?
        AND grant_expires_at>? RETURNING user_id""",
        (uid, digest(secret, 'grant', uid, row['challenge'], token), int(time.time()))).fetchone() is not None


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_email(key, sender, email, challenge, code):
    payload = {'message': {
        'recipients': [{'email': email}],
        'from_email': sender,
        'from_name': 'ВагонМесто',
        'subject': 'Код восстановления пароля ВагонМесто',
        'idempotence_key': challenge,
        'body': {'plaintext': f'Ваш код восстановления: {code}\n\n'
                              'Код действует 10 минут. Никому не сообщайте его. '
                              'Если вы не запрашивали смену пароля, проигнорируйте письмо.'}
    }}
    req = request.Request(API, json.dumps(payload).encode(),
                          {'X-API-KEY': key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=6) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get('status') != 'success' or result.get('failed_emails'):
            LOG.warning('Password reset: mail provider rejected request')
            return False
        return True
    except error.HTTPError as exc:
        LOG.warning('Password reset: mail provider HTTP %s', exc.code)
    except (OSError, ValueError):
        LOG.warning('Password reset: mail provider unavailable or invalid response')
    return False


def deliver(key, sender, uid, email, challenge, code):
    if not send_email(key, sender, email, challenge, code):
        # Keep the cooldown, but do not leave a usable code after a known delivery API failure.
        try:
            with db.transaction() as con:
                con.execute('UPDATE password_resets SET code_hash=NULL WHERE user_id=? AND challenge=?',
                            (uid, challenge))
        except (db.psycopg.Error, db.sqlite3.Error):
            LOG.warning('Password reset: could not clear failed email challenge')
