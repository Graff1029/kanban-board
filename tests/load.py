"""Locust: 1000 пользователей работают с доской на 10 000 карточек."""
import itertools
import sys
import time
import json
from pathlib import Path
from locust import HttpUser, between, task
import jwt

# При прямом запуске готовим отдельную нагрузочную базу.
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from db import connection, init_db
    from app import Register, Project, Board
    import services as s

    def prepare():
        init_db()
        with connection() as db:
            if db.execute('SELECT 1 FROM users').fetchone():
                raise SystemExit('База уже содержит пользователей. Для примера задайте другой DB_PATH.')
        admin = s.register(Register(name='Администратор', email='admin@example.ru', password='Study123!'))
        user = {'id': admin['id'], 'role': 'admin'}
        project = s.save_project(user, Project(name='Учебный проект', description='Пример для лабораторной работы'))
        board = s.save_board(user, Board(name='Задачи команды', project_id=project['id']))
        columns = s.board_state(board['id'])['columns']
        # Подготовка идёт до замера. В нагрузке не тратим время на bcrypt.
        tokens = []
        with connection(True) as db:
            password = db.execute('SELECT password FROM users WHERE id=?', (admin['id'],)).fetchone()[0]
            for number in range(1000):
                uid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,'developer')",
                                 (f'Нагрузка {number}', f'load{number}@example.ru', password)).lastrowid
                jti, exp = f'load-{number}', int(time.time()) + 86400
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (jti, uid, exp))
                tokens.append(jwt.encode({'sub': str(uid), 'jti': jti, 'exp': exp}, s.secret(), algorithm='HS256'))
            db.executemany("INSERT INTO tasks(column_id,title,priority,labels) VALUES(?,?,'medium','нагрузка')",
                           [(columns[0]['id'], f'Задача {i}') for i in range(10000)])
        Path('reports').mkdir(exist_ok=True)
        Path('reports/load-tokens.json').write_text(json.dumps({'tokens': tokens, 'board': board['id'],
                                                             'columns': [c['id'] for c in columns]}), encoding='utf-8')
        print('Готово: 10 000 карточек, 1000 пользователей. Токены для теста действуют сутки.')

    prepare()
    raise SystemExit(0)

DATA = json.loads(Path('reports/load-tokens.json').read_text(encoding='utf-8'))
TOKENS = itertools.cycle(DATA['tokens'])


class BoardUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers['Authorization'] = 'Bearer ' + next(TOKENS)

    @task(4)
    def read_board(self):
        self.client.get(f"/api/boards/{DATA['board']}", name='GET board (200 tasks)')

    @task(1)
    def crud(self):
        data = {'title': 'Нагрузочная задача', 'column_id': DATA['columns'][1]}
        response = self.client.post('/api/tasks', json=data, name='POST task')
        if response.status_code != 201:
            return
        item = response.json()
        task_id = item.pop('id')
        item['column_id'] = DATA['columns'][2]
        item['title'] = 'После изменения'
        self.client.put(f'/api/tasks/{task_id}', json=item, name='PUT task (move)')
        self.client.get(f'/api/tasks/{task_id}', name='GET task')
        self.client.delete(f'/api/tasks/{task_id}', name='DELETE task')
