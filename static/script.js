// Данные текущего экрана. Токен храним только в памяти вкладки.
const $ = id => document.getElementById(id);
let token = '', me, people = [], projects = [], boards = [], state;
let registerMode = false, editingTask = null, editingStructure, dragId = null;
let messageTimer, searchTimer, offset = 0, stateRequest = 0;
const roles = {admin: 'Администратор', manager: 'Менеджер', developer: 'Разработчик'};
const priorities = {high: 'Высокий', medium: 'Средний', low: 'Низкий'};

function notice(text, success = false) {
  const dialog = document.querySelector('dialog[open]');
  if (dialog) {
    let message = dialog.querySelector('.dialog-message');
    if (!message) { message = element('p', '', 'dialog-message'); message.setAttribute('role', 'alert'); dialog.prepend(message); }
    message.textContent = text;
  }
  $('message').textContent = text;
  $('message').className = success ? 'success' : '';
  $('message').hidden = false;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => $('message').hidden = true, 7000);
}

// Ошибка любого обработчика попадает в одно сообщение.
function run(action) {
  return (...args) => {
    try { return Promise.resolve(action(...args)).catch(error => notice(error.message)); }
    catch (error) { notice(error.message); }
  };
}

async function api(path, method = 'GET', data) {
  const headers = token ? {Authorization: 'Bearer ' + token} : {};
  const options = {method, headers};
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  const response = await fetch('/api' + path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && token) signedOut();
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Проверьте заполненные поля');
  }
  return response.json();
}

function element(tag, text, className = '') {
  const item = document.createElement(tag);
  // Пользовательский текст не вставляем как HTML.
  item.textContent = text;
  item.className = className;
  return item;
}

function button(text, action) {
  const item = element('button', text);
  item.type = 'button';
  item.onclick = run(action);
  return item;
}

function options(select, items, empty) {
  select.replaceChildren();
  if (empty !== undefined) select.add(new Option(empty, ''));
  for (const item of items) select.add(new Option(item.name, item.id));
}

function signedOut() {
  token = '';
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  $('workspace').hidden = $('account').hidden = true;
  $('auth').hidden = false;
}

function setAuthMode() {
  $('auth-title').textContent = registerMode ? 'Регистрация' : 'Вход';
  $('auth-submit').textContent = registerMode ? 'Зарегистрироваться' : 'Войти';
  $('auth-toggle').textContent = registerMode ? 'Уже есть аккаунт' : 'Создать аккаунт';
  $('name-label').hidden = !registerMode;
  $('name').required = registerMode;
  $('password').autocomplete = registerMode ? 'new-password' : 'current-password';
}
$('auth-toggle').onclick = () => { registerMode = !registerMode; setAuthMode(); };
$('auth-form').onsubmit = run(async event => {
  event.preventDefault();
  const data = {email: $('email').value, password: $('password').value};
  if (registerMode) {
    await api('/register', 'POST', {...data, name: $('name').value});
    registerMode = false;
    setAuthMode();
    notice('Аккаунт создан. Теперь войдите.', true);
    return;
  }
  token = (await api('/login', 'POST', data)).token;
  me = await api('/me');
  people = await api('/users');
  $('password').value = '';
  $('user-name').textContent = me.name + ' · ' + roles[me.role];
  $('workspace').hidden = $('account').hidden = false;
  $('auth').hidden = true;
  document.querySelectorAll('[data-manage]').forEach(item => item.hidden = me.role === 'developer');
  $('users-button').hidden = me.role !== 'admin';
  options($('filter-assignee'), people, 'Все');
  await loadProjects();
});
$('logout').onclick = run(async () => { await api('/logout', 'POST'); signedOut(); });

async function loadProjects(selected = $('project').value) {
  projects = await api('/projects');
  options($('project'), projects);
  if (projects.some(item => String(item.id) === String(selected))) $('project').value = selected;
  await loadBoards();
}
async function loadBoards(selected = $('board').value) {
  offset = 0;
  const projectId = $('project').value;
  boards = projectId ? await api('/projects/' + projectId + '/boards') : [];
  options($('board'), boards);
  if (boards.some(item => String(item.id) === String(selected))) $('board').value = selected;
  $('project-description').textContent = projects.find(item => String(item.id) === projectId)?.description || '';
  for (const id of ['project-edit', 'project-delete', 'board-add']) $(id).disabled = !projectId;
  await loadState();
}
async function loadState() {
  const request = ++stateRequest;
  const boardId = $('board').value;
  $('pagination').hidden = !boardId;
  for (const id of ['board-edit', 'board-delete', 'column-add', 'task-add']) $(id).disabled = !boardId;
  if (!boardId) {
    state = null;
    $('columns').replaceChildren(element('p', me.role === 'developer' ? 'Пока нет доски. Попросите менеджера создать её.' : 'Создайте проект и доску, чтобы добавить задачи.', 'empty'));
    return;
  }
  const filters = new URLSearchParams({q: $('search').value, label: $('filter-label').value, priority: $('filter-priority').value, offset});
  if ($('filter-assignee').value) filters.set('assignee_id', $('filter-assignee').value);
  const result = await api('/boards/' + boardId + '?' + filters);
  if (request !== stateRequest) return;
  state = result;
  $('prev-page').disabled = offset === 0;
  $('next-page').disabled = offset + 200 >= state.total;
  $('page-info').textContent = 'Задачи: ' + (state.total ? offset + 1 : 0) + '–' + Math.min(offset + 200, state.total) + ' из ' + state.total;
  renderBoard();
}
$('project').onchange = run(() => loadBoards());
$('board').onchange = run(async () => { offset = 0; await loadState(); });
$('refresh').onclick = run(loadState);
$('prev-page').onclick = run(async () => { offset = Math.max(0, offset - 200); await loadState(); });
$('next-page').onclick = run(async () => { offset += 200; await loadState(); });
for (const id of ['search', 'filter-label', 'filter-priority', 'filter-assignee']) {
  $(id).oninput = () => { offset = 0; clearTimeout(searchTimer); searchTimer = setTimeout(run(loadState), 250); };
}
$('reset').onclick = run(async () => {
  offset = 0;
  for (const id of ['search', 'filter-label', 'filter-priority', 'filter-assignee']) $(id).value = '';
  await loadState();
});

// Проекты, доски и колонки используют одно небольшое окно.
function openStructure(type, item) {
  editingStructure = {type, item};
  $('structure-title').textContent = (item ? 'Изменить: ' : 'Добавить: ') + {projects: 'проект', boards: 'доска', columns: 'колонка'}[type];
  $('structure-name').value = item?.name || '';
  $('structure-description').value = item?.description || '';
  $('structure-wip').value = item?.wip || 0;
  $('description-label').hidden = type !== 'projects';
  $('wip-label').hidden = type !== 'columns';
  $('structure-dialog').querySelector('.dialog-message')?.remove();
  $('structure-dialog').showModal();
}
for (const type of ['project', 'board', 'column']) {
  $(type + '-add').onclick = () => openStructure(type + 's');
  if (type === 'column') continue;
  $(type + '-edit').onclick = () => openStructure(type + 's', (type === 'project' ? projects : boards).find(item => String(item.id) === $(type).value));
  $(type + '-delete').onclick = run(async () => {
    if (!confirm('Удалить ' + (type === 'project' ? 'проект' : 'доску') + ' вместе со всеми задачами?')) return;
    await api('/' + type + 's/' + $(type).value, 'DELETE');
    await loadProjects();
  });
}
$('structure-form').onsubmit = run(async event => {
  event.preventDefault();
  const {type, item} = editingStructure;
  const data = {name: $('structure-name').value};
  if (type === 'projects') data.description = $('structure-description').value;
  if (type === 'boards') data.project_id = Number($('project').value);
  if (type === 'columns') { data.board_id = Number($('board').value); data.wip = Number($('structure-wip').value); }
  const result = await api('/' + type + (item ? '/' + item.id : ''), item ? 'PUT' : 'POST', data);
  $('structure-dialog').close();
  if (type === 'projects') await loadProjects(String(result.id));
  else if (type === 'boards') await loadBoards(String(result.id));
  else await loadState();
});

function renderBoard() {
  $('columns').replaceChildren();
  $('task-add').disabled = !state.columns.length;
  if (!state.columns.length) $('columns').append(element('p', 'Добавьте хотя бы одну колонку.', 'empty'));
  state.columns.forEach((column, index) => {
    const box = element('section', '', 'column');
    box.dataset.id = column.id;
    const title = element('h2', column.name);
    title.append(element('span', column.total + (column.wip ? '/' + column.wip : '')));
    box.append(title);
    if (me.role !== 'developer') {
      const tools = element('div', '', 'column-tools');
      const shift = async step => {
        const ids = state.columns.map(item => item.id);
        [ids[index], ids[index + step]] = [ids[index + step], ids[index]];
        await api('/boards/' + state.board.id + '/order', 'PUT', {ids});
        await loadState();
      };
      const left = button('←', () => shift(-1)), right = button('→', () => shift(1));
      left.setAttribute('aria-label', 'Сдвинуть колонку влево');
      right.setAttribute('aria-label', 'Сдвинуть колонку вправо');
      left.disabled = index === 0;
      right.disabled = index === state.columns.length - 1;
      tools.append(left, right, button('Изменить', () => openStructure('columns', column)), button('Удалить', async () => {
        if (!confirm('Удалить пустую колонку?')) return;
        await api('/columns/' + column.id, 'DELETE'); await loadState();
      }));
      box.append(tools);
    }
    const tasks = state.tasks.filter(task => task.column_id === column.id);
    for (const task of tasks) {
      const card = element('article', '', 'card ' + task.priority);
      card.draggable = true;
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.setAttribute('aria-label', 'Открыть задачу: ' + task.title);
      card.append(element('h3', task.title));
      card.append(element('p', '#' + task.id + ' · ' + priorities[task.priority]));
      const person = people.find(item => item.id === task.assignee_id);
      card.append(element('p', person?.name || 'Без исполнителя', 'muted'));
      if (task.labels) card.append(element('p', task.labels, 'muted'));
      if (task.due_date) {
        const today = new Date().toLocaleDateString('sv-SE');
        card.append(element('p', 'Срок: ' + task.due_date, task.due_date < today ? 'overdue' : 'muted'));
      }
      card.onclick = run(() => openTask(task));
      card.onkeydown = event => { if (event.key === 'Enter') run(() => openTask(task))(); };
      card.ondragstart = event => { dragId = task.id; event.dataTransfer.setData('text/plain', String(task.id)); };
      card.ondragend = () => { dragId = null; document.querySelectorAll('.drag-over').forEach(item => item.classList.remove('drag-over')); };
      box.append(card);
    }
    if (!tasks.length) box.append(element('p', 'Задач нет', 'empty'));
    box.append(button('+ Задача', () => openTask(null, column.id)));
    box.ondragover = event => { event.preventDefault(); box.classList.add('drag-over'); };
    box.ondragleave = () => box.classList.remove('drag-over');
    box.ondrop = run(async event => {
      event.preventDefault(); box.classList.remove('drag-over');
      const task = state.tasks.find(item => item.id === dragId);
      if (!task || task.column_id === column.id) return;
      const data = taskData(task);
      data.column_id = column.id;
      await api('/tasks/' + task.id, 'PUT', data);
      await loadState();
    });
    $('columns').append(box);
  });
}
function taskData(task) {
  const result = {};
  for (const key of ['title', 'description', 'column_id', 'assignee_id', 'priority', 'due_date', 'labels']) result[key] = task[key];
  return result;
}
async function openTask(task, columnId) {
  editingTask = task?.id || null;
  options($('task-column'), state.columns);
  options($('task-assignee'), people, 'Не назначен');
  $('task-title').value = task?.title || '';
  $('task-description').value = task?.description || '';
  $('task-column').value = task?.column_id || columnId || state.columns[0].id;
  $('task-assignee').value = task?.assignee_id || '';
  $('task-priority').value = task?.priority || 'medium';
  $('task-due').value = task?.due_date || '';
  $('task-labels').value = task?.labels || '';
  $('task-heading').textContent = task ? 'Задача #' + task.id : 'Новая задача';
  $('task-extra').hidden = $('task-delete').hidden = !task;
  $('comment-text').value = $('file').value = '';
  if (task) await loadDetails();
  $('task-dialog').querySelector('.dialog-message')?.remove();
  $('task-dialog').showModal();
}
$('task-add').onclick = run(() => openTask());
$('task-form').onsubmit = run(async event => {
  event.preventDefault();
  const data = {title: $('task-title').value, description: $('task-description').value, column_id: Number($('task-column').value),
    assignee_id: Number($('task-assignee').value) || null, priority: $('task-priority').value, due_date: $('task-due').value || null, labels: $('task-labels').value};
  await api('/tasks' + (editingTask ? '/' + editingTask : ''), editingTask ? 'PUT' : 'POST', data);
  $('task-dialog').close();
  await loadState();
});
$('task-delete').onclick = run(async () => {
  if (!confirm('Удалить задачу со всеми комментариями и вложениями?')) return;
  await api('/tasks/' + editingTask, 'DELETE'); $('task-dialog').close(); await loadState();
});
async function loadDetails() {
  const task = await api('/tasks/' + editingTask);
  for (const group of ['comments', 'history']) {
    $(group).replaceChildren();
    for (const item of task[group]) {
      const row = element('div', '', 'entry');
      row.append(element('small', item.name + ' · ' + item.created_at + ' UTC'), element('span', item.text));
      $(group).append(row);
    }
    if (!task[group].length) $(group).append(element('p', 'Пока нет записей', 'muted'));
  }
  $('attachments').replaceChildren();
  for (const file of task.attachments) {
    const row = element('div', '', 'file-row');
    row.append(button(file.name, async () => {
      const response = await fetch('/api/attachments/' + file.id, {headers: {Authorization: 'Bearer ' + token}});
      if (!response.ok) throw new Error('Не удалось скачать файл');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = file.name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }), button('Удалить', async () => { await api('/attachments/' + file.id, 'DELETE'); await loadDetails(); }));
    $('attachments').append(row);
  }
}
$('comment-form').onsubmit = run(async event => {
  event.preventDefault(); await api('/tasks/' + editingTask + '/comments', 'POST', {text: $('comment-text').value});
  $('comment-text').value = ''; await loadDetails();
});
$('file-form').onsubmit = run(async event => {
  event.preventDefault();
  const file = $('file').files[0];
  if (!file || file.size > 5 * 1024 * 1024) throw new Error('Выберите файл до 5 МБ');
  const data = new FormData(); data.append('file', file);
  await api('/tasks/' + editingTask + '/attachments', 'POST', data);
  $('file').value = ''; await loadDetails();
});
$('users-button').onclick = run(async () => {
  people = await api('/users'); $('users-list').replaceChildren();
  for (const person of people) {
    const row = element('div', '', 'user-row'), select = document.createElement('select');
    for (const [value, title] of Object.entries(roles)) select.add(new Option(title, value));
    select.value = person.role; select.setAttribute('aria-label', 'Роль ' + person.name);
    row.append(element('span', person.name + ' (' + person.email + ')'), select, button('OK', async () => {
      await api('/users/' + person.id + '/role', 'PATCH', {role: select.value});
      notice('Роль сохранена. Войдите заново, чтобы обновить кнопки участника.', true);
      if (person.id === me.id) { $('users-dialog').close(); signedOut(); }
    }));
    $('users-list').append(row);
  }
  $('users-dialog').showModal();
});
document.querySelectorAll('[data-close]').forEach(item => item.onclick = () => $(item.dataset.close).close());
