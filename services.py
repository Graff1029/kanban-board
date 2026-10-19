"""Правила доски: права, вход, лимиты и задачи."""
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
import bcrypt
import jwt
from fastapi import HTTPException
from db import connection, db_path, rows

ROLES = ('admin', 'manager', 'developer')


def fail(code, text):
    raise HTTPException(code, text)


def require(user, *roles):
    if user['role'] not in roles:
        fail(403, 'Недостаточно прав')


def get(db, table, item_id):
    # Названия таблиц задаёт только сервер. Значения передаём через ?.
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
    if row is None:
        fail(404, 'Запись не найдена')
    return dict(row)


def secret():
    value = os.getenv('JWT_SECRET')
    if value:
        if len(value) < 32:
            raise RuntimeError('JWT_SECRET должен содержать хотя бы 32 символа')
        return value
    # Для локального запуска сохраняем случайный ключ рядом с базой.
    path = db_path().parent / '.jwt-secret'
    try:
        with path.open('x') as file:
            file.write(secrets.token_hex(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    return path.read_text().strip()


def register(data):
    email = data.email.casefold()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        fail(422, 'Введите нормальный email')
    if len(data.password.encode()) > 72:
        fail(422, 'Пароль длиннее 72 байт')
    password = bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode()
    with connection(True) as db:
        role = 'developer' if db.execute('SELECT 1 FROM users').fetchone() else 'admin'
        try:
            item = db.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',
                              (data.name, email, password, role))
        except sqlite3.IntegrityError:
            fail(409, 'Этот email уже зарегистрирован')
        return {'id': item.lastrowid, 'role': role}


def login(data):
    with connection(True) as db:
        user = db.execute('SELECT * FROM users WHERE email=?', (data.email.casefold(),)).fetchone()
        # Пустая проверка тоже занимает время, чтобы сложнее было угадывать email.
        password_hash = user['password'] if user else DUMMY_HASH
        valid = len(data.password.encode()) <= 72 and bcrypt.checkpw(data.password.encode(), password_hash.encode())
        if not user or not valid:
            fail(401, 'Неверный email или пароль')
        expires, jti = int(time.time()) + 8 * 3600, secrets.token_hex(16)
        db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (jti, user['id'], expires))
        token = jwt.encode({'sub': str(user['id']), 'jti': jti, 'exp': expires}, secret(), algorithm='HS256')
        return {'token': token}


DUMMY_HASH = bcrypt.hashpw(b'not-a-real-password', bcrypt.gensalt()).decode()


def current_user(token):
    try:
        payload = jwt.decode(token, secret(), algorithms=['HS256'], options={'require': ['sub', 'exp', 'jti']})
        user_id = int(payload['sub'])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        fail(401, 'Войдите заново')
    with connection() as db:
        session = db.execute('SELECT 1 FROM sessions WHERE jti=? AND user_id=? AND expires>?',
                             (payload['jti'], user_id, int(time.time()))).fetchone()
        if not session:
            fail(401, 'Сеанс завершён')
        user = get(db, 'users', user_id)
        return {'id': user['id'], 'name': user['name'], 'role': user['role'], 'jti': payload['jti']}


def logout(user):
    with connection(True) as db:
        db.execute('DELETE FROM sessions WHERE jti=?', (user['jti'],))
    return {'ok': True}


def users():
    with connection() as db:
        return rows(db, 'SELECT id,name,email,role FROM users ORDER BY name')


def change_role(user, item_id, role):
    require(user, 'admin')
    with connection(True) as db:
        old = get(db, 'users', item_id)
        if old['role'] == 'admin' and role != 'admin':
            if db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 1:
                fail(409, 'Нельзя убрать последнего администратора')
        db.execute('UPDATE users SET role=? WHERE id=?', (role, item_id))
    return {'ok': True}


def projects():
    with connection() as db:
        return rows(db, 'SELECT * FROM projects ORDER BY id')


def save_project(user, data, item_id=None):
    require(user, 'admin', 'manager')
    with connection(True) as db:
        if item_id is not None:
            get(db, 'projects', item_id)
            db.execute('UPDATE projects SET name=?,description=? WHERE id=?', (data.name, data.description, item_id))
        else:
            item_id = db.execute('INSERT INTO projects(name,description) VALUES(?,?)', (data.name, data.description)).lastrowid
        return get(db, 'projects', item_id)


def boards(project_id):
    with connection() as db:
        get(db, 'projects', project_id)
        return rows(db, 'SELECT * FROM boards WHERE project_id=? ORDER BY id', (project_id,))


def save_board(user, data, item_id=None):
    require(user, 'admin', 'manager')
    with connection(True) as db:
        get(db, 'projects', data.project_id)
        if item_id is not None:
            old = get(db, 'boards', item_id)
            if old['project_id'] != data.project_id:
                fail(422, 'Перенос доски в другой проект запрещён')
            db.execute('UPDATE boards SET name=? WHERE id=?', (data.name, item_id))
        else:
            item_id = db.execute('INSERT INTO boards(project_id,name) VALUES(?,?)', (data.project_id, data.name)).lastrowid
            for position, name in enumerate(('Нужно сделать', 'В работе', 'Готово')):
                db.execute('INSERT INTO columns(board_id,name,position) VALUES(?,?,?)', (item_id, name, position))
        return get(db, 'boards', item_id)


def save_column(user, data, item_id=None):
    require(user, 'admin', 'manager')
    with connection(True) as db:
        get(db, 'boards', data.board_id)
        if item_id is not None:
            old = get(db, 'columns', item_id)
            if old['board_id'] != data.board_id:
                fail(422, 'Перенос колонки на другую доску запрещён')
            count = db.execute('SELECT COUNT(*) FROM tasks WHERE column_id=?', (item_id,)).fetchone()[0]
            if data.wip and count > data.wip:
                fail(409, 'В колонке уже больше задач, чем новый лимит')
            db.execute('UPDATE columns SET name=?,wip=? WHERE id=?', (data.name, data.wip, item_id))
        else:
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM columns WHERE board_id=?', (data.board_id,)).fetchone()[0]
            item_id = db.execute('INSERT INTO columns(board_id,name,wip,position) VALUES(?,?,?,?)',
                                 (data.board_id, data.name, data.wip, position)).lastrowid
        return get(db, 'columns', item_id)


def reorder(user, board_id, ids):
    require(user, 'admin', 'manager')
    with connection(True) as db:
        get(db, 'boards', board_id)
        actual = [c['id'] for c in rows(db, 'SELECT id FROM columns WHERE board_id=?', (board_id,))]
        if len(ids) != len(set(ids)) or set(ids) != set(actual):
            fail(422, 'Нужно передать все колонки доски без повторов')
        for position, item_id in enumerate(ids):
            db.execute('UPDATE columns SET position=? WHERE id=?', (position, item_id))
    return {'ok': True}


def delete_structure(user, table, item_id):
    require(user, 'admin', 'manager')
    with connection(True) as db:
        get(db, table, item_id)
        if table == 'columns' and db.execute('SELECT 1 FROM tasks WHERE column_id=?', (item_id,)).fetchone():
            fail(409, 'Сначала перенесите или удалите задачи из колонки')
        db.execute(f'DELETE FROM {table} WHERE id=?', (item_id,))
    return {'ok': True}


def board_state(board_id, q='', assignee_id=None, label='', priority='', limit=200, offset=0):
    with connection() as db:
        board = get(db, 'boards', board_id)
        columns = rows(db, 'SELECT *, (SELECT COUNT(*) FROM tasks WHERE column_id=columns.id) AS total '
                           'FROM columns WHERE board_id=? ORDER BY position,id', (board_id,))
        sql = 'SELECT t.* FROM tasks t JOIN columns c ON c.id=t.column_id WHERE c.board_id=?'
        args = [board_id]
        if q:
            sql += ' AND (instr(casefold(t.title),casefold(?))>0 OR instr(casefold(t.description),casefold(?))>0)'
            args += [q, q]
        if assignee_id is not None:
            sql += ' AND t.assignee_id=?'
            args.append(assignee_id)
        if label:
            sql += " AND instr(',' || casefold(replace(t.labels,', ',',')) || ',', ',' || casefold(?) || ',')>0"
            args.append(label.strip())
        if priority:
            sql += ' AND t.priority=?'
            args.append(priority)
        total = db.execute('SELECT COUNT(*) FROM (' + sql + ')', args).fetchone()[0]
        return {'board': board, 'columns': columns, 'total': total,
                'tasks': rows(db, sql + ' ORDER BY t.id LIMIT ? OFFSET ?', [*args, limit, offset])}


def log(db, task_id, user, text):
    db.execute('INSERT INTO history(task_id,user_id,text) VALUES(?,?,?)', (task_id, user['id'], text))


def save_task(user, data, item_id=None):
    with connection(True) as db:
        column = get(db, 'columns', data.column_id)
        old = get(db, 'tasks', item_id) if item_id is not None else None
        if old:
            source = get(db, 'columns', old['column_id'])
            if source['board_id'] != column['board_id']:
                fail(422, 'Перенос задачи на другую доску запрещён')
        if data.assignee_id is not None:
            get(db, 'users', data.assignee_id)
        if column['wip'] and (not old or old['column_id'] != data.column_id):
            count = db.execute('SELECT COUNT(*) FROM tasks WHERE column_id=?', (data.column_id,)).fetchone()[0]
            if count >= column['wip']:
                fail(409, 'Достигнут WIP-лимит колонки')
        fields = data.model_dump(mode='json')
        fields['labels'] = ', '.join(dict.fromkeys(x.strip() for x in data.labels.split(',') if x.strip()))
        if old:
            db.execute('UPDATE tasks SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?', (*fields.values(), item_id))
            changed = [key for key, value in fields.items() if old[key] != value]
            if changed:
                names = {'title': 'название', 'description': 'описание', 'column_id': 'колонка', 'assignee_id': 'исполнитель',
                         'priority': 'приоритет', 'due_date': 'срок', 'labels': 'метки'}
                log(db, item_id, user, 'Изменено: ' + ', '.join(names[key] for key in changed))
        else:
            item_id = db.execute('INSERT INTO tasks(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')', tuple(fields.values())).lastrowid
            log(db, item_id, user, 'Задача создана')
        return get(db, 'tasks', item_id)


def delete_task(user, item_id):
    with connection(True) as db:
        get(db, 'tasks', item_id)
        db.execute('DELETE FROM tasks WHERE id=?', (item_id,))
    return {'ok': True}


def task_detail(item_id):
    with connection() as db:
        task = get(db, 'tasks', item_id)
        for table in ('comments', 'history'):
            task[table] = rows(db, f'SELECT x.*,u.name FROM {table} x JOIN users u ON u.id=x.user_id '
                                  'WHERE task_id=? ORDER BY x.id', (item_id,))
        task['attachments'] = rows(db, 'SELECT id,name FROM attachments WHERE task_id=? ORDER BY id', (item_id,))
        return task


def add_comment(user, item_id, text):
    with connection(True) as db:
        get(db, 'tasks', item_id)
        comment_id = db.execute('INSERT INTO comments(task_id,user_id,text) VALUES(?,?,?)', (item_id, user['id'], text)).lastrowid
        log(db, item_id, user, 'Добавлен комментарий')
        return {'id': comment_id}


def attach(user, task_id, name, content):
    name = Path((name or 'file').replace('\\', '/')).name
    types = {'.txt': 'text/plain', '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
    kind = types.get(Path(name).suffix.casefold())
    if not kind or len(name) > 150 or any(ord(x) < 32 for x in name):
        fail(422, 'Можно прикрепить TXT, PDF, PNG или JPG с коротким именем')
    if not content or len(content) > 5 * 1024 * 1024:
        fail(413, 'Размер файла должен быть от 1 байта до 5 МБ')
    with connection(True) as db:
        get(db, 'tasks', task_id)
        item_id = db.execute('INSERT INTO attachments(task_id,user_id,name,data,content_type) VALUES(?,?,?,?,?)',
                             (task_id, user['id'], name, content, kind)).lastrowid
        log(db, task_id, user, 'Добавлен файл: ' + name)
        return {'id': item_id, 'name': name}


def attachment(item_id):
    with connection() as db:
        return get(db, 'attachments', item_id)


def delete_attachment(user, item_id):
    with connection(True) as db:
        file = get(db, 'attachments', item_id)
        if file['user_id'] != user['id']:
            require(user, 'admin', 'manager')
        db.execute('DELETE FROM attachments WHERE id=?', (item_id,))
        log(db, file['task_id'], user, 'Удалён файл: ' + file['name'])
    return {'ok': True}
