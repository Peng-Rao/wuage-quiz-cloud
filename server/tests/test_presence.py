"""Presence follows live sessions, while last-seen history survives revocation."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api import auth as auth_api
from app.auth import COOKIE, current_user, token_hash
from app.db import LoginSession, SessionLocal, User, add_missing_columns, utcnow
from app.main import app
from .test_auth import accounts, sign_in  # noqa: F401


def account_status(client, user):
    response = client.get('/api/users')
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return next(row for row in response.json() if row['id'] == user.id)


def test_login_heartbeat_timeout_and_logout(accounts, monkeypatch):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    assert account_status(admin, target)['lastSeenAt'] is None
    assert account_status(admin, target)['isOnline'] is False
    member = sign_in(target)
    status = account_status(admin, target)
    assert status['isOnline'] is True
    initial = datetime.fromisoformat(status['lastSeenAt'].replace('Z', '+00:00'))
    with SessionLocal() as s:
        assert s.get(LoginSession, token_hash(member.cookies[COOKIE])).last_seen_at == initial
    monkeypatch.setattr(auth_api, 'utcnow', lambda: initial + timedelta(seconds=90))
    assert account_status(admin, target)['isOnline'] is True
    monkeypatch.setattr(auth_api, 'utcnow', lambda: initial + timedelta(seconds=91))
    assert account_status(admin, target)['isOnline'] is False
    assert account_status(admin, target)['lastSeenAt'] == status['lastSeenAt']
    response = member.post('/api/auth/heartbeat')
    assert response.status_code == 204 and response.content == b''
    renewed = account_status(admin, target)
    assert renewed['isOnline'] is True
    assert datetime.fromisoformat(renewed['lastSeenAt'].replace('Z', '+00:00')) == initial + timedelta(seconds=91)
    assert member.post('/api/auth/logout').status_code == 204
    offline = account_status(admin, target)
    assert offline['isOnline'] is False and offline['lastSeenAt'] == renewed['lastSeenAt']


def test_multiple_sessions_and_expiration(accounts):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    first = sign_in(target)
    second = sign_in(target)
    assert first.post('/api/auth/logout').status_code == 204
    assert account_status(admin, target)['isOnline'] is True
    with SessionLocal() as s:
        s.get(LoginSession, token_hash(second.cookies[COOKIE])).expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    assert account_status(admin, target)['isOnline'] is False
    assert second.post('/api/auth/heartbeat').status_code == 401


@pytest.mark.parametrize('active', [True, False])
def test_revoked_sessions_keep_last_seen(accounts, active):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    member = sign_in(target)
    previous = account_status(admin, target)['lastSeenAt']
    assert admin.put(f'/api/users/{target.id}', json={
        'displayName': target.display_name, 'role': 'member', 'subjects': ['数学'], 'active': active,
    }).status_code == 200
    status = account_status(admin, target)
    assert status['isOnline'] is False and status['lastSeenAt'] == previous
    assert member.post('/api/auth/heartbeat').status_code == 401


def test_presence_is_admin_only_and_heartbeat_checks_origin(accounts):
    anonymous = TestClient(app)
    assert anonymous.get('/api/users').status_code == 401
    assert anonymous.post('/api/auth/heartbeat').status_code == 401
    for role in ['leader', 'member']:
        client = sign_in(accounts[role])
        assert client.get('/api/users').status_code == 403
        assert client.post('/api/auth/heartbeat', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/auth/heartbeat').status_code == 204
        assert 'lastSeenAt' not in client.get('/api/auth/me').json()
        if role == 'leader':
            assert all('lastSeenAt' not in row and 'isOnline' not in row
                       for row in client.get('/api/review-recipients').json())


def test_revocation_between_authentication_and_heartbeat(accounts, monkeypatch):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    member = sign_in(target)
    cookie = member.cookies[COOKIE]
    previous = account_status(admin, target)['lastSeenAt']
    assert member.post('/api/auth/logout').status_code == 204
    member.cookies.set(COOKIE, cookie)
    monkeypatch.setitem(app.dependency_overrides, current_user, lambda: target)
    try:
        assert member.post('/api/auth/heartbeat').status_code == 401
    finally:
        app.dependency_overrides.pop(current_user)
    assert account_status(admin, target)['lastSeenAt'] == previous
    with SessionLocal() as s:
        assert not list(s.scalars(select(LoginSession).where(LoginSession.user_id == target.id)))


def test_presence_timestamp_does_not_regress(accounts, monkeypatch):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    member = sign_in(target)
    previous = account_status(admin, target)['lastSeenAt']
    initial = datetime.fromisoformat(previous.replace('Z', '+00:00'))
    monkeypatch.setattr(auth_api, 'utcnow', lambda: initial - timedelta(seconds=1))
    assert member.post('/api/auth/heartbeat').status_code == 204
    assert account_status(admin, target)['lastSeenAt'] == previous
    with SessionLocal() as s:
        assert s.get(LoginSession, token_hash(member.cookies[COOKIE])).last_seen_at == initial


def test_old_sessions_are_unknown_until_first_heartbeat(accounts):
    admin = sign_in(accounts['admin'])
    target = accounts['member']
    member = sign_in(target)
    with SessionLocal() as s:
        s.get(User, target.id).last_seen_at = None
        s.get(LoginSession, token_hash(member.cookies[COOKIE])).last_seen_at = None
        s.commit()
    status = account_status(admin, target)
    assert status['isOnline'] is False and status['lastSeenAt'] is None
    assert member.post('/api/auth/heartbeat').status_code == 204
    assert account_status(admin, target)['isOnline'] is True


def test_presence_migration_preserves_existing_rows(scratch_engine):
    with scratch_engine.begin() as conn:
        conn.execute(text('CREATE TABLE app_user (id VARCHAR(32) PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE login_session (token_hash VARCHAR(64) PRIMARY KEY)'))
        conn.execute(text("INSERT INTO app_user (id) VALUES ('old-user')"))
        conn.execute(text("INSERT INTO login_session (token_hash) VALUES ('old-session')"))
    added = add_missing_columns(scratch_engine)
    assert 'app_user.last_seen_at' in added and 'login_session.last_seen_at' in added
    assert add_missing_columns(scratch_engine) == []
    with scratch_engine.connect() as conn:
        assert conn.execute(text('SELECT last_seen_at FROM app_user')).one() == (None,)
        assert conn.execute(text('SELECT last_seen_at FROM login_session')).one() == (None,)


def test_presence_timestamp_output_is_utc():
    value = datetime(2026, 10, 5, 12, tzinfo=timezone(timedelta(hours=8)))
    user = auth_api.AdminUserOut(id='test', username='test', display_name='测试', role='member',
                                subjects=['数学'], active=True, is_online=False, last_seen_at=value)
    assert user.model_dump(mode='json', by_alias=True)['lastSeenAt'] == '2026-10-05T04:00:00Z'
