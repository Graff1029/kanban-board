"""Запуск: python tests/e2e.py. База и сервер для теста временные."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'


def login(page, email):
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Пароль', exact=True).fill('Study123!')
    page.get_by_role('button', name='Войти', exact=True).click()
    expect(page.locator('#workspace')).to_be_visible()


def register(page, email, name):
    page.get_by_role('button', name='Создать аккаунт', exact=True).click()
    page.get_by_label('Имя', exact=True).fill(name)
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Пароль', exact=True).fill('Study123!')
    page.get_by_role('button', name='Зарегистрироваться', exact=True).click()
    expect(page.locator('#auth-title')).to_have_text('Вход')


def structure(page, button, name):
    page.get_by_role('button', name=button, exact=True).click()
    dialog = page.locator('#structure-dialog')
    dialog.get_by_label('Название', exact=True).fill(name)
    dialog.get_by_role('button', name='Сохранить', exact=True).click()
    expect(dialog).not_to_be_visible()


def scenario(browser, url, name):
    context = browser.new_context(viewport={'width': 1366, 'height': 850})
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(url)
        register(page, f'admin-{name}@test.ru', 'Администратор')
        login(page, f'admin-{name}@test.ru')
        structure(page, '+ Проект', 'Учебный проект')
        structure(page, '+ Доска', 'Первая доска')
        expect(page.locator('.column')).to_have_count(3)
        page.get_by_role('button', name='+ Задача', exact=True).first.click()
        dialog = page.locator('#task-dialog')
        dialog.get_by_label('Название', exact=True).fill('Проверить доску')
        dialog.get_by_label('Метки через запятую').fill('учёба')
        dialog.get_by_role('button', name='Сохранить', exact=True).click()
        card = page.locator('.card').filter(has_text='Проверить доску')
        expect(card).to_be_visible()
        card.drag_to(page.locator('.column').nth(1))
        expect(page.locator('.column').nth(1).locator('.card')).to_have_count(1)
        card.click()
        page.get_by_label('Текст комментария').fill('<img src=x onerror=alert(1)>')
        page.get_by_role('button', name='Отправить', exact=True).click()
        expect(page.locator('#comments')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('#comments img')).to_have_count(0)
        page.get_by_label('Вложение', exact=True).set_input_files({'name': 'test.txt', 'mimeType': 'text/plain', 'buffer': b'hello'})
        page.get_by_role('button', name='Прикрепить', exact=True).click()
        expect(page.locator('#attachments')).to_contain_text('test.txt')
        with page.expect_download() as download:
            page.get_by_role('button', name='test.txt', exact=True).click()
        assert Path(download.value.path()).read_bytes() == b'hello'
        page.get_by_role('button', name='Закрыть', exact=True).click()
        page.get_by_label('Поиск', exact=True).fill('несуществующее')
        expect(page.locator('.card')).to_have_count(0)
        page.get_by_role('button', name='Сбросить', exact=True).click()
        expect(page.locator('.card')).to_have_count(1)
        # Лимит заполненной колонки нельзя обойти через форму.
        page.locator('.column').nth(1).get_by_role('button', name='Изменить', exact=True).click()
        page.get_by_label('WIP-лимит (0 = без лимита)').fill('1')
        page.locator('#structure-dialog').get_by_role('button', name='Сохранить', exact=True).click()
        expect(page.locator('#structure-dialog')).not_to_be_visible()
        page.locator('.column').nth(1).get_by_role('button', name='+ Задача', exact=True).click()
        dialog.get_by_label('Название', exact=True).fill('Лишняя')
        dialog.get_by_role('button', name='Сохранить', exact=True).click()
        expect(dialog.locator('[role=alert]')).to_contain_text('WIP-лимит')
        dialog.get_by_role('button', name='Закрыть', exact=True).click()
        # На телефоне главная страница помещается по ширине.
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1366, 'height': 850})
        page.get_by_role('button', name='Выйти', exact=True).click()
        register(page, f'dev-{name}@test.ru', 'Разработчик')
        login(page, f'dev-{name}@test.ru')
        expect(page.locator('#project-delete')).not_to_be_visible()
        expect(page.locator('#users-button')).not_to_be_visible()
        expect(page.locator('.card')).to_have_count(1)
        page.get_by_role('button', name='Выйти', exact=True).click()
        login(page, f'admin-{name}@test.ru')
        assert not errors, errors
        if os.getenv('CAPTURE_DEMO'):
            page.screenshot(path=str(ROOT.parent / '.work' / 'board.png'), full_page=True)
        page.get_by_role('button', name='Выйти', exact=True).click()
        print(name + ': PASS, регистрация, роли, CRUD, drag-and-drop, WIP, XSS, файл, мобильная ширина')
    except Exception:
        page.screenshot(path=str(REPORTS / f'{name}-failed.png'), full_page=True)
        raise
    finally:
        context.tracing.stop(path=str(REPORTS / f'{name}-trace.zip'))
        context.close()


def main():
    REPORTS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        for name in os.getenv('E2E_BROWSERS', 'chromium').split(','):
            with tempfile.TemporaryDirectory() as directory, socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                sock.close()
                url = f'http://127.0.0.1:{port}'
                env = {**os.environ, 'DB_PATH': str(Path(directory) / 'e2e.db'), 'JWT_SECRET': 'e2e-test-secret-at-least-32-characters'}
                server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port)], cwd=ROOT,
                                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                browser = None
                try:
                    for attempt in range(100):
                        try:
                            if httpx.get(url + '/api/health', timeout=1, trust_env=False).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    else:
                        raise RuntimeError('Тестовый сервер не запустился')
                    if name in ('chrome', 'msedge'):
                        browser = playwright.chromium.launch(channel=name)
                    else:
                        launch = {'executable_path': os.environ['BROWSER_PATH']} if os.getenv('BROWSER_PATH') and name == 'chromium' else {}
                        browser = getattr(playwright, name).launch(**launch)
                    scenario(browser, url, name)
                finally:
                    if browser:
                        browser.close()
                    server.terminate()
                    server.wait(timeout=10)


if __name__ == '__main__':
    main()
