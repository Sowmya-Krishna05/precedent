@echo off
REM Precedent v2 setup for Windows. Double-click, or run "setup.bat" in this folder.
setlocal
cd /d "%~dp0"

echo.
echo === Precedent v2 setup ===
echo Folder: %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment .venv ...
  python -m venv .venv || goto :fail
)

echo Installing packages ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :fail

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo Created .env - Notepad will open. Paste your HINDSIGHT_API_KEY and GROQ_API_KEY, save, close Notepad.
  notepad ".env"
)

echo.
echo Checking everything ...
".venv\Scripts\python.exe" -m app.doctor
echo.
echo Next steps, in this folder:
echo   .venv\Scripts\activate
echo   python scripts\replay.py      (first time only, about 4 minutes)
echo   python -m app.server          (then open http://localhost:8000)
echo.
pause
exit /b 0

:fail
echo.
echo Setup failed. Copy the messages above and send them over.
pause
exit /b 1
