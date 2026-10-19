@echo off
rem Go to the project folder.
cd /d "%~dp0"
rem Create a separate Python environment if it does not exist.
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto error
rem Install the libraries from the list.
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
rem Start the local server.
echo Open http://127.0.0.1:8000 in your browser
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
exit /b
:error
echo Check that Python is installed and the internet connection works.
pause
