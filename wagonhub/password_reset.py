"""Email challenges and short-lived, single-use password reset grants."""
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.headerregistry import Address

from email_validator import EmailNotValidError, validate_email

from . import db

SMTP_HOST = 'postbox.cloud.yandex.net'
SMTP_PORT = 465
SMTP_TIMEOUT = 5
LOG = logging.getLogger(__name__)
TTL = 600
RESEND_AFTER = 60
MAX_ATTEMPTS = 5


def settings():
    username = os.getenv('POSTBOX_SMTP_USER', '').strip()
    password = os.getenv('POSTBOX_SMTP_PASSWORD', '').strip()
    sender = os.getenv('RESET_FROM_EMAIL', '').strip()
    secret = os.getenv('PASSWORD_RESET_SECRET', '').strip()
    if not (username and password and sender and len(secret) >= 32):
        return None
    try:
        sender = validate_email(sender, check_deliverability=False, allow_smtputf8=False).normalized
    except EmailNotValidError:
        return None
    return username, password, sender, secret


def pad_request_time(started):
    # Ordinary requests, unknown emails and cooldowns have a similar minimum response time.
    remaining = 3.0 - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


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


def send_email(username, password, sender, email, code):
    message = EmailMessage()
    message['From'] = Address('ВагонМесто', addr_spec=sender)
    message['To'] = email
    message['Subject'] = 'Код восстановления пароля ВагонМесто'
    message.set_content(
        f'Ваш код восстановления: {code}\n\n'
        'Код действует 10 минут. Никому не сообщайте его. '
        'Если вы не запрашивали смену пароля, проигнорируйте письмо.'
    )
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(username, password)
            return not smtp.send_message(message, from_addr=sender, to_addrs=[email])
    except smtplib.SMTPResponseException as exc:
        LOG.warning('Password reset: Postbox SMTP %s', exc.smtp_code)
    except (OSError, ValueError):
        LOG.warning('Password reset: Postbox unavailable or unable to send')
    return False


def deliver(username, password, sender, uid, email, challenge, code):
    if not send_email(username, password, sender, email, code):
        # Keep the cooldown, but do not leave a usable code after a known delivery API failure.
        try:
            with db.transaction() as con:
                con.execute('UPDATE password_resets SET code_hash=NULL WHERE user_id=? AND challenge=?',
                            (uid, challenge))
        except (db.psycopg.Error, db.sqlite3.Error):
            LOG.warning('Password reset: could not clear failed email challenge')
