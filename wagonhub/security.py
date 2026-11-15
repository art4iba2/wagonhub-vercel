"""Production settings and protection for the pilot's account endpoints."""
import hmac
import json
import os
import re
import time

from fastapi import HTTPException

from . import db

COOKIE = 'wagonhub_session'


def production():
    return os.getenv('APP_ENV') == 'production' or bool(os.getenv('VERCEL'))


def admin_email():
    return os.getenv('PILOT_ADMIN_EMAIL', '').strip().lower()


def is_admin(user):
    return bool(admin_email() and user['email'] == admin_email())


def invitations():
    raw = os.getenv('PILOT_INVITES_JSON', '').strip() or '{}'
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        for email, entry in data.items():
            if (email != email.strip().lower() or '@' not in email
                    or not isinstance(entry, dict) or entry.get('role') not in ('track', 'wagon')
                    or not isinstance(entry.get('code'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', entry['code'])
                    or (entry['role'] == 'wagon' and not re.fullmatch(
                        r'(?:[0-9]{10}|[0-9]{12})', str(entry.get('inn', ''))))):
                raise ValueError
        return data
    except (TypeError, ValueError):
        raise RuntimeError('Проверьте формат PILOT_INVITES_JSON') from None


def allowed_signup(email, role, code):
    entry = invitations().get(email)
    return bool(entry and entry['role'] == role and hmac.compare_digest(
        entry['code'].encode(), code.encode()))


def throttle(action, request, email):
    # Trust proxy headers only from the proxy addresses configured in Uvicorn.
    ip = request.client.host if request.client else 'unknown'
    bucket = int(time.time()) // 600
    reached = False
    with db.transaction() as con:
        con.execute('DELETE FROM auth_limits WHERE bucket < ?', (bucket - 1,))
        for key, maximum in ((f'{action}:ip:{ip}', 30), (f'{action}:email:{email}', 8)):
            row = con.execute('''INSERT INTO auth_limits(key,bucket,attempts) VALUES (?,?,1)
                ON CONFLICT(key,bucket) DO UPDATE SET attempts=auth_limits.attempts+1
                RETURNING attempts''', (key, bucket)).fetchone()
            if row['attempts'] > maximum:
                reached = True
                break
    if reached:
        raise HTTPException(429, 'Слишком много попыток. Повторите позже.',
                            headers={'Retry-After': str(600 - int(time.time()) % 600)})
