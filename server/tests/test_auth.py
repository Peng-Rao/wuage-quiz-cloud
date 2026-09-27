"""Real cookie sessions and permission checks, including indirect data access."""
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import COOKIE, hash_password, token_hash
from app.db import BankQuestion, KnowledgeNode, KnowledgeTree, LoginSession, LoginThrottle, SessionLocal, User, utcnow
from app.main import app
from app.storage import get_store
from .test_bank import _paper

PASSWORD = "Permission-test-123"


@pytest.fixture()
def accounts():
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as s:
        result = {}
        for role, subjects, label in [("admin", [], "admin"), ("leader", ["数学"], "leader"),
                                       ("member", ["数学"], "member"), ("member", ["数学"], "other")]:
            user = User(id=uuid.uuid4().hex, username=f"{label}-{tag}", display_name=label, role=role,
                        subjects=subjects, active=True, password_hash=hash_password(PASSWORD))
            s.add(user); result[label] = user
        s.commit()
    yield result
    with SessionLocal() as s:
        for row in s.scalars(select(LoginThrottle)):
            s.delete(row)
        s.commit()


def sign_in(user):
    client = TestClient(app)
    response = client.post('/api/auth/login', json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def data(accounts):
    with SessionLocal() as s:
        tree = KnowledgeTree(id=uuid.uuid4().hex, school_id='demo', name='权限测试树', subject='数学', stage='高中',
                             textbook='', builtin=False, node_count=3)
        s.add(tree); s.flush()
        nodes = []
        for i, name in enumerate(['公开给本人', '未分配知识点', '其他人知识点']):
            node = KnowledgeNode(id=uuid.uuid4().hex, tree_id=tree.id, name=name, path=name, level=1, seq=i, is_leaf=True)
            s.add(node); nodes.append(node)
        s.flush()
        math = _paper(s, {"subject": "数学", "stage": "高中", "title": '权限数学卷', "region": "北京"},
                      [{"type": "单选题", "score": 5, "stem": f"权限题{i}", "coef": .5,
                        "kps": [{"id": n.id, "name": n.name, "path": n.path}]} for i, n in enumerate(nodes)])
        chem = _paper(s, {"subject": "化学", "stage": "高中", "title": '权限化学卷', "region": "上海"},
                      [{"type": "单选题", "score": 5, "stem": "化学机密", "coef": .5}])
        s.flush()
        qs = list(s.scalars(select(BankQuestion).where(BankQuestion.source_job_id == math).order_by(BankQuestion.source_no)))
        qs[0].owner_id = accounts['member'].id; qs[0].reviewed_at = utcnow(); qs[0].reviewed_by = accounts['leader'].id
        qs[1].owner_id = accounts['member'].id
        qs[2].owner_id = accounts['other'].id; qs[2].reviewed_at = utcnow()
        qs[0].images = [f'jobs/{math}/images/allowed.png']
        s.commit()
        result = dict(math=math, chem=chem, questions=[q.id for q in qs], tree=tree.id, nodes=[n.id for n in nodes])
    for key in [f'jobs/{math}/images/allowed.png', f'jobs/{math}/pages/1.png', f'jobs/{chem}/pages/1.png']:
        get_store().put(key, b'png')
    return result


def test_anonymous_and_session_lifecycle(accounts):
    anon = TestClient(app)
    for path in ['/api/bank/questions', '/api/papers', '/api/parse-jobs', '/api/knowledge-trees', '/api/users']:
        assert anon.get(path).status_code == 401
    client = sign_in(accounts['member'])
    cookie = client.cookies[COOKIE]
    assert client.get('/api/auth/me').json()['role'] == 'member'
    with SessionLocal() as s:
        assert s.get(LoginSession, token_hash(cookie)) is not None
        assert s.get(LoginSession, cookie) is None
    assert client.post('/api/auth/logout').status_code == 204
    client.cookies.set(COOKIE, cookie)
    assert client.get('/api/auth/me').status_code == 401
    client = sign_in(accounts['member'])
    with SessionLocal() as s:
        session = s.get(LoginSession, token_hash(client.cookies[COOKIE]))
        session.expires_at = utcnow() - timedelta(seconds=1); s.commit()
    assert client.get('/api/auth/me').status_code == 401


def test_member_scope_every_surface(accounts, data):
    c = sign_in(accounts['member'])
    got = c.get('/api/bank/questions').json()
    assert got['total'] == 1 and [q['id'] for q in got['items']] == data['questions'][:1]
    assert c.get('/api/bank/questions?offset=1').json()['items'] == []
    papers = c.get('/api/papers').json()
    assert papers['total'] == 1 and papers['items'][0]['questionCount'] == 1
    detail = c.get(f"/api/papers/{data['math']}").json()
    assert len(detail['questions']) == 1 and detail['sourceQuestionCount'] == 1
    assert c.get(f"/api/papers/{data['chem']}").status_code == 404
    facets = c.get('/api/bank/facets').json()
    assert [r['name'] for r in facets['regions']] == ['北京']
    counts = c.get('/api/bank/knowledge-counts', params={'treeId': data['tree']}).json()
    assert counts == {data['nodes'][0]: 1}
    tree = c.get(f"/api/knowledge-trees/{data['tree']}").json()
    assert [n['id'] for n in tree['nodes']] == data['nodes'][:1]
    hits = c.get('/api/knowledge/search', params={'treeId': data['tree'], 'q': '未分配知识点'}).json()
    assert hits == []
    assert c.get(f"/api/files/jobs/{data['math']}/images/allowed.png").status_code == 200
    assert c.get(f"/api/files/jobs/{data['math']}/pages/1.png").status_code == 403
    assert c.get(f"/api/files/jobs/{data['chem']}/pages/1.png").status_code == 404
    for path, body in [('/api/uploads', {}), ('/api/parse-jobs', {}), ('/api/similar/search', {}),
                       ('/api/papers/compose', {}), ('/api/bank/review', {}), ('/api/users', {})]:
        assert c.post(path, json=body).status_code == 403
    assert c.delete(f"/api/papers/{data['math']}").status_code == 403
    assert c.get(f"/api/parse-jobs/{data['math']}").status_code == 403


def test_leader_scope_and_review(accounts, data):
    c = sign_in(accounts['leader'])
    assert c.get('/api/bank/questions?subject=化学').json()['total'] == 0
    assert c.get(f"/api/papers/{data['chem']}").status_code == 404
    assert c.get(f"/api/parse-jobs/{data['chem']}").status_code == 404
    assert c.delete(f"/api/papers/{data['chem']}").status_code == 404
    assert c.get('/api/users').status_code == 403
    assert c.get('/api/usage/summary').status_code == 403
    assert c.post('/api/eval-runs', json={}).status_code == 403
    assert c.put(f"/api/parse-jobs/{data['math']}/meta", json={'subject': '化学'}).status_code == 403
    payload = {'questionIds': data['questions'][1:2], 'ownerId': accounts['member'].id, 'approved': True}
    assert c.post('/api/bank/review', json=payload).status_code == 200
    member = sign_in(accounts['member'])
    assert member.get('/api/bank/questions').json()['total'] == 2
    payload['approved'] = False
    assert c.post('/api/bank/review', json=payload).status_code == 200
    assert member.get('/api/bank/questions').json()['total'] == 1
    assert c.post('/api/bank/review', json={**payload, 'ownerId': accounts['admin'].id}).status_code == 403
    assert c.post('/api/papers/compose', json={'subject': '化学', 'stage': '高中'}).status_code == 403


def test_admin_account_changes_revoke_sessions(accounts):
    admin = sign_in(accounts['admin']); member = sign_in(accounts['member'])
    payload = {'displayName': '新姓名', 'role': 'leader', 'subjects': ['语文'], 'active': True}
    assert admin.put(f"/api/users/{accounts['member'].id}", json=payload).status_code == 200
    assert member.get('/api/auth/me').status_code == 401
    member = sign_in(accounts['member'])
    assert member.get('/api/auth/me').json()['subjects'] == ['语文']
    payload['active'] = False
    assert admin.put(f"/api/users/{accounts['member'].id}", json=payload).status_code == 200
    assert member.get('/api/auth/me').status_code == 401
    assert member.post('/api/auth/login', json={'username': accounts['member'].username, 'password': PASSWORD}).status_code == 401
    assert admin.put(f"/api/users/{accounts['admin'].id}", json=payload).status_code == 400
    assert admin.post('/api/users', json={'username': accounts['admin'].username, 'displayName': 'dup',
                      'role': 'admin', 'password': PASSWORD}).status_code == 409


def test_login_security(accounts):
    c = TestClient(app)
    body = {'username': accounts['member'].username, 'password': PASSWORD}
    assert c.post('/api/auth/login', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    r = c.post('/api/auth/login', json=body)
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=lax' in r.headers['set-cookie']
    assert 'password' not in r.text and 'token' not in r.text
    assert c.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    for _ in range(10):
        assert c.post('/api/auth/login', json={**body, 'password': 'wrong'}).status_code == 401
    assert c.post('/api/auth/login', json=body).status_code == 429


def test_upload_tickets_are_owned(accounts):
    leader = sign_in(accounts['leader']); admin = sign_in(accounts['admin'])
    ticket = admin.post('/api/uploads', json={'fileName': 'test.pdf', 'fileSize': 3, 'contentType': 'application/pdf'}).json()
    assert leader.put(ticket['uploadUrl'], content=b'pdf').status_code == 403
    assert admin.put(ticket['uploadUrl'], content=b'pdf').status_code == 204
    assert leader.post('/api/parse-jobs', json={'fileKeys': [ticket['fileKey']], 'fileNames': ['test.pdf'],
                                               'options': {'subject': '数学'}}).status_code == 403


def test_export_rechecks_revoked_review(accounts, data):
    member = sign_in(accounts['member']); leader = sign_in(accounts['leader'])
    body = {'ids': data['questions'][:1]}
    assert member.post('/api/basket/validate', json=body).status_code == 204
    leader.post('/api/bank/review', json={'questionIds': body['ids'], 'ownerId': accounts['member'].id, 'approved': False})
    assert member.post('/api/basket/validate', json=body).status_code == 403


def test_ai_compose_validates_output_and_scope(accounts, data, monkeypatch):
    from app.api import review
    from app.config import Settings
    settings = Settings(llm_base_url='https://test.invalid', llm_api_key='test', llm_model='test')
    monkeypatch.setattr(review, 'get_settings', lambda: settings)
    captured = []
    async def choose(system, payload, settings, **kwargs):
        import json
        candidates = json.loads(payload)['questions']
        assert all(q['stem'] != '化学机密' for q in candidates)
        captured.extend(candidates)
        return {'ids': [candidates[0]['id']]}
    monkeypatch.setattr(review, 'chat_json', choose)
    leader = sign_in(accounts['leader'])
    body = {'subject': '数学', 'stage': '高中', 'count': 1, 'difficulty': '适中'}
    result = leader.post('/api/papers/compose', json=body)
    assert result.status_code == 200 and result.json()['total'] == 1
    assert captured
    async def invalid(*args, **kwargs):
        return {'ids': ['unauthorized-id']}
    monkeypatch.setattr(review, 'chat_json', invalid)
    assert leader.post('/api/papers/compose', json=body).status_code == 502
    assert sign_in(accounts['member']).post('/api/papers/compose', json=body).status_code == 403


def test_leader_upload_parse_commit_and_filtered_job_counts(accounts, data):
    from app.db import ParseBatch, ParseJob
    from .fixtures import make_exam_pdf
    from .test_api import upload, wait_done
    leader = sign_in(accounts['leader'])
    with SessionLocal() as s:
        batch = ParseBatch(id=uuid.uuid4().hex[:16], school_id='demo', total=2)
        s.add(batch)
        for jid in [data['math'], data['chem']]:
            s.get(ParseJob, jid).batch_id = batch.id
        s.commit()
        batch_id = batch.id
    page = leader.get('/api/parse-jobs', params={'batchId': batch_id}).json()
    assert page['total'] == 1 and len(page['items']) == 1
    assert leader.get(f'/api/parse-batches/{batch_id}').json()['total'] == 1
    key = upload(leader, '数学.pdf', make_exam_pdf())
    with TestClient(app) as running:
        login = running.post('/api/auth/login', json={'username': accounts['leader'].username, 'password': PASSWORD})
        assert login.status_code == 200
        created = running.post('/api/parse-jobs', json={'fileKeys': [key], 'fileNames': ['数学.pdf'],
                                                       'options': {'subject': '数学'}})
        assert created.status_code == 200, created.text
        job = wait_done(running, created.json()['id'])
        assert job['status'] == 'done', job
        questions = running.get(f"/api/parse-jobs/{job['id']}/questions").json()
        result = running.post(f"/api/parse-jobs/{job['id']}/commit", json={'questionIds': [q['id'] for q in questions], 'force': True})
        assert result.status_code == 200 and result.json()['savedCount'] == len(questions)
        assert running.get(f"/api/papers/{job['id']}").status_code == 200
