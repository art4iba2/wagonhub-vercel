"""One-use pilot invitations. The database keeps only token hashes."""
import hashlib
import secrets
import time

from . import security

TTL_SECONDS = 7 * 86400


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue(con, admin_id, email, role, inn):
    now = int(time.time())
    if con.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
        return None
    con.execute("""UPDATE pilot_invites SET revoked_at=? WHERE email=? AND expires_at<=?
        AND claimed_at IS NULL AND revoked_at IS NULL""", (now, email, now))
    token = secrets.token_urlsafe(32)
    invite_id = secrets.token_hex(16)
    expires_at = now + TTL_SECONDS
    row = con.execute("""INSERT INTO pilot_invites(id,email,role,inn,token_hash,created_at,expires_at,created_by)
        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id""",
        (invite_id, email, role, inn, token_hash(token), now, expires_at, admin_id)).fetchone()
    return {'id': invite_id, 'token': token, 'expires_at': expires_at} if row else None


def claim(con, email, role, token):
    now = int(time.time())
    return con.execute("""UPDATE pilot_invites SET claimed_at=?
        WHERE token_hash=? AND email=? AND role=? AND expires_at>?
        AND claimed_at IS NULL AND revoked_at IS NULL RETURNING id""",
        (now, token_hash(token), email, role, now)).fetchone()


def assign(con, invite_id, user_id):
    con.execute('UPDATE pilot_invites SET claimed_user_id=? WHERE id=?', (user_id, invite_id))


def membership(con, user):
    row = con.execute('SELECT role,inn FROM pilot_invites WHERE claimed_user_id=?',
                      (user['id'],)).fetchone()
    if row and row['role'] == user['role']:
        return dict(row)
    legacy = security.invitations().get(user['email'], {})
    return legacy if legacy.get('role') == user['role'] else None


def revoke(con, invite_id):
    now = int(time.time())
    return con.execute("""UPDATE pilot_invites SET revoked_at=?
        WHERE id=? AND claimed_at IS NULL AND revoked_at IS NULL AND expires_at>?
        RETURNING id""", (now, invite_id, now)).fetchone()


def recent(con):
    now = int(time.time())
    return [dict(row) for row in con.execute("""SELECT id,email,role,inn,created_at,expires_at,
        CASE WHEN claimed_at IS NOT NULL THEN 'used'
             WHEN revoked_at IS NOT NULL THEN 'revoked'
             WHEN expires_at<=? THEN 'expired' ELSE 'pending' END AS status
        FROM pilot_invites ORDER BY created_at DESC,id DESC LIMIT 100""", (now,)).fetchall()]
