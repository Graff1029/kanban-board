"""Проверяем настоящие запросы и отдельную временную базу."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time
import bcrypt
import jwt
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import app, Task, Column
from db import connection, init_db
from services import require, get
import services as s

PASSWORD = 'Study123!'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setenv('JWT_SECRET', 'test-secret-that-is-longer-than-32-characters')
    with TestClient(app) as client:
        yield client


def account(client, email='admin@test.ru', name='Студент'):
    result = client.post('/api/register', json={'email': email, 'password': PASSWORD, 'name': name})
    assert result.status_code == 201
    login = client.post('/api/login', json={'email': email, 'password': PASSWORD})
    assert login.status_code == 200
    return {'Authorization': 'Bearer ' + login.json()['token']}, result.json()['id']


def setup_board(client):
    headers, user_id = account(client)
    project = client.post('/api/projects', headers=headers, json={'name': 'Учёба', 'description': 'Лабораторные'}).json()
    board = client.post('/api/boards', headers=headers, json={'project_id': project['id'], 'name': 'Задания'}).json()
    state = client.get('/api/boards/' + str(board['id']), headers=headers).json()
    return headers, user_id, project, board, state['columns']


def task(client, headers, column, **extra):
    return client.post('/api/tasks', headers=headers, json={'title': 'Сделать сайт', 'column_id': column, **extra})


def test_auth_and_logout(client):
    headers, user_id = account(client)
    assert client.get('/api/me', headers=headers).json()['role'] == 'admin'
    with connection() as db:
        stored = db.execute('SELECT password FROM users WHERE id=?', (user_id,)).fetchone()[0]
        assert stored != PASSWORD and bcrypt.checkpw(PASSWORD.encode(), stored.encode())
    assert client.post('/api/register', json={'email': 'ADMIN@test.ru', 'password': PASSWORD, 'name': 'Друг'}).status_code == 409
    for email, password in [('missing@test.ru', PASSWORD), ('admin@test.ru', 'badpass1'), ('admin@test.ru', 'я' * 40)]:
        assert client.post('/api/login', json={'email': email, 'password': password}).status_code == 401
    assert client.post('/api/logout', headers=headers).status_code == 200
    assert client.get('/api/me', headers=headers).status_code == 401
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/me', headers={'Authorization': 'Basic bad'}).status_code == 401
    assert client.get('/api/me', headers={'Authorization': 'Bearer bad'}).status_code == 401


@pytest.mark.parametrize('payload', [
    {'email': 'bad', 'password': PASSWORD, 'name': 'Иван'},
    {'email': 'ok@test.ru', 'password': '123', 'name': 'Иван'},
    {'email': 'ok@test.ru', 'password': PASSWORD, 'name': '  '},
    {'email': 'ok@test.ru', 'password': 'я' * 40, 'name': 'Иван'},
    {'email': 'ok@test.ru', 'password': PASSWORD, 'name': 'Иван', 'role': 'admin'},
])
def test_invalid_register(client, payload):
    assert client.post('/api/register', json=payload).status_code == 422


def test_roles(client):
    admin, admin_id, project, board, columns = setup_board(client)
    dev, dev_id = account(client, 'dev@test.ru')
    assert client.get('/api/me', headers=dev).json()['role'] == 'developer'
    assert client.get('/api/users', headers=dev).status_code == 200
    forbidden = [('POST', '/api/projects', {'name': 'Запрет'}),
                 ('PUT', f"/api/projects/{project['id']}", {'name': 'Запрет'}),
                 ('DELETE', f"/api/projects/{project['id']}", None),
                 ('POST', '/api/boards', {'name': 'Запрет', 'project_id': project['id']}),
                 ('POST', '/api/columns', {'name': 'Запрет', 'board_id': board['id']}),
                 ('PATCH', f'/api/users/{dev_id}/role', {'role': 'admin'})]
    for method, url, data in forbidden:
        assert client.request(method, url, headers=dev, json=data).status_code == 403
    assert task(client, dev, columns[0]['id']).status_code == 201
    assert client.patch(f'/api/users/{admin_id}/role', headers=admin, json={'role': 'developer'}).status_code == 409
    assert client.patch(f'/api/users/{dev_id}/role', headers=admin, json={'role': 'manager'}).status_code == 200
    assert client.post('/api/projects', headers=dev, json={'name': 'Менеджер может'}).status_code == 201
    assert client.patch(f'/api/users/{dev_id}/role', headers=admin, json={'role': 'admin'}).status_code == 200
    assert client.patch(f'/api/users/{admin_id}/role', headers=admin, json={'role': 'developer'}).status_code == 200
    assert client.post('/api/projects', headers=admin, json={'name': 'Теперь нельзя'}).status_code == 403


def test_project_board_column_crud(client):
    h, _, p, b, cols = setup_board(client)
    assert client.get('/api/projects', headers=h).json()[0]['name'] == 'Учёба'
    assert client.put(f"/api/projects/{p['id']}", headers=h, json={'name': 'Новый проект'}).status_code == 200
    assert client.get(f"/api/projects/{p['id']}/boards", headers=h).json()[0]['id'] == b['id']
    assert client.put(f"/api/boards/{b['id']}", headers=h, json={'name': 'Новая доска', 'project_id': p['id']}).status_code == 200
    col = client.post('/api/columns', headers=h, json={'name': 'Проверка', 'board_id': b['id']}).json()
    ids = [col['id']] + [c['id'] for c in cols]
    assert client.put(f"/api/boards/{b['id']}/order", headers=h, json={'ids': ids}).status_code == 200
    assert client.get(f"/api/boards/{b['id']}", headers=h).json()['columns'][0]['id'] == col['id']
    assert client.put(f"/api/columns/{col['id']}", headers=h, json={'name': 'Тест', 'board_id': b['id'], 'wip': 2}).status_code == 200
    for bad in [[col['id']], ids + [col['id']], [999] + ids[1:]]:
        assert client.put(f"/api/boards/{b['id']}/order", headers=h, json={'ids': bad}).status_code == 422
    assert client.delete(f"/api/columns/{col['id']}", headers=h).status_code == 200
    assert client.delete(f"/api/boards/{b['id']}", headers=h).status_code == 200
    assert client.get(f"/api/boards/{b['id']}", headers=h).status_code == 404
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 200
    assert client.delete(f"/api/projects/{p['id']}", headers=h).status_code == 404


def test_tasks_filters_comments_files(client):
    h, uid, p, b, cols = setup_board(client)
    created = task(client, h, cols[0]['id'], title='Русский САЙТ', description='Нужно сверстать',
                   labels='учёба,  веб, учёба', assignee_id=uid, priority='high', due_date='2026-12-01')
    assert created.status_code == 201
    item = created.json()
    assert item['labels'] == 'учёба, веб'
    task(client, h, cols[0]['id'], title='Другая')
    for query in ['q=сайт', 'q=СВЕРСТАТЬ', f'assignee_id={uid}', 'label=ВЕБ', 'priority=high']:
        found = client.get(f"/api/boards/{b['id']}?{query}", headers=h).json()
        assert len(found['tasks']) == 1 and found['tasks'][0]['id'] == item['id']
        assert found['columns'][0]['total'] == 2
    assert not client.get(f"/api/boards/{b['id']}?q=%27%20OR%201=1--", headers=h).json()['tasks']
    assert client.post(f"/api/tasks/{item['id']}/comments", headers=h, json={'text': '<script>alert(1)</script>'}).status_code == 201
    file = client.post(f"/api/tasks/{item['id']}/attachments", headers=h, files={'file': ('../../текст.txt', b'hello')})
    assert file.status_code == 201
    file_id = file.json()['id']
    downloaded = client.get(f'/api/attachments/{file_id}', headers=h)
    assert downloaded.content == b'hello' and 'attachment;' in downloaded.headers['Content-Disposition']
    assert client.get(f'/api/attachments/{file_id}').status_code == 401
    details = client.get(f"/api/tasks/{item['id']}", headers=h).json()
    assert details['comments'][0]['text'].startswith('<script>')
    assert details['attachments'][0]['name'] == 'текст.txt'
    assert len(details['history']) == 3
    del item['id']
    item.update(title='Изменено', column_id=cols[1]['id'], priority='low', due_date=None, assignee_id=None)
    url = '/api/tasks/' + str(created.json()['id'])
    assert client.put(url, headers=h, json=item).status_code == 200
    count = len(client.get(url, headers=h).json()['history'])
    assert client.put(url, headers=h, json=item).status_code == 200
    assert len(client.get(url, headers=h).json()['history']) == count
    dev, _ = account(client, 'other@test.ru')
    assert client.delete(f'/api/attachments/{file_id}', headers=dev).status_code == 403
    assert client.delete(f'/api/attachments/{file_id}', headers=h).status_code == 200
    own = client.post(url + '/attachments', headers=dev, files={'file': ('own.txt', b'own')}).json()['id']
    assert client.delete(f'/api/attachments/{own}', headers=dev).status_code == 200
    assert client.delete(url, headers=h).status_code == 200
    assert client.get(url, headers=h).status_code == 404
    # Каскад не оставляет комментарии и историю удалённой задачи.
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM comments').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM history').fetchone()[0] == 1


def test_wip_and_wrong_board(client):
    h, _, p, b, cols = setup_board(client)
    target, source = cols[1]['id'], cols[0]['id']
    config = {'name': 'В работе', 'board_id': b['id'], 'wip': 1}
    assert client.put(f'/api/columns/{target}', headers=h, json=config).status_code == 200
    first = task(client, h, target).json()
    assert task(client, h, target).status_code == 409
    second = task(client, h, source).json()
    item_id = second.pop('id')
    second['column_id'] = target
    assert client.put(f'/api/tasks/{item_id}', headers=h, json=second).status_code == 409
    first_id = first.pop('id')
    assert client.put(f'/api/tasks/{first_id}', headers=h, json=first).status_code == 200
    assert client.delete(f'/api/columns/{source}', headers=h).status_code == 409
    task(client, h, source)
    assert client.put(f'/api/columns/{source}', headers=h, json={**config, 'wip': 1}).status_code == 409
    other_project = client.post('/api/projects', headers=h, json={'name': 'Другой'}).json()['id']
    other = client.post('/api/boards', headers=h, json={'name': 'Другая', 'project_id': other_project}).json()['id']
    other_column = client.get(f'/api/boards/{other}', headers=h).json()['columns'][0]['id']
    assert client.put(f"/api/boards/{b['id']}", headers=h, json={'name': 'Нет', 'project_id': other_project}).status_code == 422
    assert client.put(f'/api/columns/{target}', headers=h, json={**config, 'board_id': other}).status_code == 422
    assert client.put(f'/api/tasks/{first_id}', headers=h, json={**first, 'column_id': other_column}).status_code == 422
    assert task(client, h, source, assignee_id=999).status_code == 404
    assert task(client, h, 999).status_code == 404


def test_concurrent_wip(client):
    h, uid, p, b, cols = setup_board(client)
    column = cols[0]['id']
    client.put(f'/api/columns/{column}', headers=h, json={'name': 'Одна задача', 'board_id': b['id'], 'wip': 1})
    user = {'id': uid, 'role': 'admin'}

    def create(number):
        try:
            s.save_task(user, Task(title=str(number), column_id=column))
            return 201
        except Exception as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [201, 409]


@pytest.mark.parametrize('extra', [{'title': ''}, {'title': '  '}, {'priority': 'urgent'}, {'due_date': 'bad'},
                                    {'assignee_id': 0}, {'labels': 'x' * 201}, {'unexpected': True}])
def test_invalid_tasks(client, extra):
    h, _, _, _, cols = setup_board(client)
    assert task(client, h, cols[0]['id'], **extra).status_code == 422


@pytest.mark.parametrize('name,content,status', [('bad.html', b'<h1>x</h1>', 422), ('empty.txt', b'', 413),
                                               ('big.pdf', b'x' * (5 * 1024 * 1024 + 1), 413),
                                               ('x' * 151 + '.txt', b'x', 422), ('bad\n.txt', b'x', 422)])
def test_invalid_files(client, name, content, status):
    h, _, _, _, cols = setup_board(client)
    item_id = task(client, h, cols[0]['id']).json()['id']
    # Сервис тоже проверяет имя, даже если запрос пришёл не из браузера.
    with pytest.raises(Exception) as error:
        s.attach({'id': 1}, item_id, name, content)
    assert error.value.status_code == status


def test_jwt_headers_static_and_migration(client, monkeypatch, tmp_path):
    h, uid = account(client)
    init_db()
    assert client.get('/').status_code == 200
    assert client.get('/static/script.js').status_code == 200
    assert client.get('/docs').status_code == 200
    assert client.get('/redoc').status_code == 200
    assert client.get('/openapi.json').json()['info']['version'] == '1.0'
    assert client.get('/api/health').json()['status'] == 'ok'
    response = client.get('/api/health', headers={'X-Forwarded-Proto': 'https'})
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert "object-src 'none'" in response.headers['Content-Security-Policy']
    with TestClient(app, base_url='https://testserver') as secure:
        assert 'Strict-Transport-Security' in secure.get('/').headers
    for payload in [{'sub': str(uid), 'jti': 'x', 'exp': int(time.time()) - 10},
                    {'sub': 'bad', 'jti': 'x', 'exp': int(time.time()) + 10},
                    {'sub': str(uid), 'jti': 'x'}]:
        token = jwt.encode(payload, s.secret(), algorithm='HS256')
        assert client.get('/api/me', headers={'Authorization': 'Bearer ' + token}).status_code == 401
    # При исключении транзакция откатывается.
    with pytest.raises(sqlite3.IntegrityError):
        with connection(True) as db:
            db.execute("INSERT INTO projects(name) VALUES('rollback')")
            db.execute('INSERT INTO boards(project_id,name) VALUES(999,?)', ('bad',))
    assert not client.get('/api/projects', headers=h).json()
    monkeypatch.delenv('JWT_SECRET')
    first = s.secret()
    assert first == s.secret() and len(first) == 64
    monkeypatch.setenv('JWT_SECRET', 'short')
    with pytest.raises(RuntimeError):
        s.secret()


def test_ten_thousand_cards_and_pagination(client):
    h, _, _, b, cols = setup_board(client)
    with connection(True) as db:
        db.executemany("INSERT INTO tasks(title,column_id,priority) VALUES(?,?,'medium')",
                       [(f'Карточка {number:05}', cols[0]['id']) for number in range(10000)])
    first = client.get(f"/api/boards/{b['id']}", headers=h).json()
    last = client.get(f"/api/boards/{b['id']}?offset=9800", headers=h).json()
    assert first['total'] == last['total'] == 10000
    assert len(first['tasks']) == len(last['tasks']) == 200
    assert last['tasks'][-1]['title'] == 'Карточка 09999'
    assert not set(t['id'] for t in first['tasks']) & set(t['id'] for t in last['tasks'])
    assert client.get(f"/api/boards/{b['id']}?limit=201", headers=h).status_code == 422
    assert client.get(f"/api/boards/{b['id']}?offset=-1", headers=h).status_code == 422
    assert client.get(f"/api/boards/{b['id']}?q=КАРТОЧКА%2009999", headers=h).json()['total'] == 1


@pytest.mark.parametrize('role,allowed', [('admin', True), ('manager', True), ('developer', False)])
def test_role_rule(role, allowed):
    if allowed:
        require({'role': role}, 'admin', 'manager')
    else:
        with pytest.raises(HTTPException) as error:
            require({'role': role}, 'admin', 'manager')
        assert error.value.status_code == 403


def test_task_validation():
    task = Task(title='  Учиться  ', column_id=1, due_date='2026-12-01')
    assert task.title == 'Учиться'
    assert task.model_dump(mode='json')['due_date'] == '2026-12-01'
    with pytest.raises(ValidationError):
        Column(name='В работе', board_id=1, wip=-1)


def test_migration_and_repository(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'unit.db'))
    init_db()
    init_db()
    with connection(True) as db:
        item = db.execute('INSERT INTO projects(name) VALUES(?)', ('Проект',)).lastrowid
        assert get(db, 'projects', item)['name'] == 'Проект'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        with pytest.raises(HTTPException) as error:
            get(db, 'projects', 999)
        assert error.value.status_code == 404
