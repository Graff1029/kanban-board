"""API доски и проверка входных данных."""
# Загайнов Александр, Воронов Савва, Серикова Анастасия
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from urllib.parse import quote
from fastapi import Depends, FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from db import ROOT, init_db
import services as s


# Проверяем поля, которые приходят с формы.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Model):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=150)]
    password: str = Field(min_length=8, max_length=72)


class Register(Login):
    name: Name


class Project(Model):
    name: Name
    description: str = Field(default='', max_length=3000)


class Board(Model):
    name: Name
    project_id: int = Field(gt=0)


class Column(Model):
    name: Name
    board_id: int = Field(gt=0)
    wip: int = Field(default=0, ge=0, le=10000)


class Order(Model):
    ids: list[int] = Field(min_length=1, max_length=100)


class Task(Model):
    title: Name
    column_id: int = Field(gt=0)
    description: str = Field(default='', max_length=5000)
    assignee_id: int | None = Field(default=None, gt=0)
    priority: Literal['low', 'medium', 'high'] = 'medium'
    due_date: date | None = None
    labels: str = Field(default='', max_length=200)


class Comment(Model):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Role(Model):
    role: Literal['admin', 'manager', 'developer']


@asynccontextmanager
async def lifespan(app):
    init_db()
    s.secret()
    yield


app = FastAPI(title='Kanban-доска', version='1.0', lifespan=lifespan)
auth = HTTPBearer(auto_error=False)


def current(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth)]):
    if not credentials or credentials.scheme.lower() != 'bearer':
        s.fail(401, 'Сначала войдите в аккаунт')
    return s.current_user(credentials.credentials)


User = Annotated[dict, Depends(current)]


@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    policy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    if request.url.path in ('/docs', '/redoc'):
        # Swagger берёт свои стили и скрипты с CDN.
        policy = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
    response.headers['Content-Security-Policy'] = policy
    if request.url.scheme == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.post('/api/register', status_code=201)
def register(data: Register):
    return s.register(data)


@app.post('/api/login')
def login(data: Login):
    return s.login(data)


@app.post('/api/logout')
def logout(user: User):
    return s.logout(user)


@app.get('/api/me')
def me(user: User):
    return {key: user[key] for key in ('id', 'name', 'role')}


@app.get('/api/users')
def users(user: User):
    return s.users()


@app.patch('/api/users/{item_id}/role')
def role(item_id: int, data: Role, user: User):
    return s.change_role(user, item_id, data.role)


@app.get('/api/projects')
def projects(user: User):
    return s.projects()


@app.post('/api/projects', status_code=201)
def add_project(data: Project, user: User):
    return s.save_project(user, data)


@app.put('/api/projects/{item_id}')
def edit_project(item_id: int, data: Project, user: User):
    return s.save_project(user, data, item_id)


@app.delete('/api/projects/{item_id}')
def delete_project(item_id: int, user: User):
    return s.delete_structure(user, 'projects', item_id)


@app.get('/api/projects/{item_id}/boards')
def boards(item_id: int, user: User):
    return s.boards(item_id)


@app.post('/api/boards', status_code=201)
def add_board(data: Board, user: User):
    return s.save_board(user, data)


@app.put('/api/boards/{item_id}')
def edit_board(item_id: int, data: Board, user: User):
    return s.save_board(user, data, item_id)


@app.delete('/api/boards/{item_id}')
def delete_board(item_id: int, user: User):
    return s.delete_structure(user, 'boards', item_id)


@app.get('/api/boards/{item_id}')
def state(item_id: int, user: User, q: str = '', assignee_id: int | None = None, label: str = '', priority: str = '',
          limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)):
    return s.board_state(item_id, q, assignee_id, label, priority, limit, offset)


@app.put('/api/boards/{item_id}/order')
def order(item_id: int, data: Order, user: User):
    return s.reorder(user, item_id, data.ids)


@app.post('/api/columns', status_code=201)
def add_column(data: Column, user: User):
    return s.save_column(user, data)


@app.put('/api/columns/{item_id}')
def edit_column(item_id: int, data: Column, user: User):
    return s.save_column(user, data, item_id)


@app.delete('/api/columns/{item_id}')
def delete_column(item_id: int, user: User):
    return s.delete_structure(user, 'columns', item_id)


@app.get('/api/tasks/{item_id}')
def task(item_id: int, user: User):
    return s.task_detail(item_id)


@app.post('/api/tasks', status_code=201)
def add_task(data: Task, user: User):
    return s.save_task(user, data)


@app.put('/api/tasks/{item_id}')
def edit_task(item_id: int, data: Task, user: User):
    return s.save_task(user, data, item_id)


@app.delete('/api/tasks/{item_id}')
def delete_task(item_id: int, user: User):
    return s.delete_task(user, item_id)


@app.post('/api/tasks/{item_id}/comments', status_code=201)
def comment(item_id: int, data: Comment, user: User):
    return s.add_comment(user, item_id, data.text)


@app.post('/api/tasks/{item_id}/attachments', status_code=201)
async def upload(item_id: int, user: User, file: Annotated[UploadFile, File()]):
    content = await file.read(5 * 1024 * 1024 + 1)
    await file.close()
    # SQLite синхронный, поэтому не блокируем цикл обработки запросов.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(s.attach, user, item_id, file.filename, content)


@app.get('/api/attachments/{item_id}')
def download(item_id: int, user: User):
    file = s.attachment(item_id)
    return Response(file['data'], media_type=file['content_type'], headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(file['name'], safe='')})


@app.delete('/api/attachments/{item_id}')
def delete_attachment(item_id: int, user: User):
    return s.delete_attachment(user, item_id)


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
