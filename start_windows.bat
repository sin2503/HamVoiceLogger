@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
  goto :eof
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 main.py
  goto :eof
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python main.py
  goto :eof
)

echo Python が見つかりません。
echo   py -3 -m venv .venv
echo   .venv\Scripts\activate
echo   python -m pip install -r requirements.txt
pause
exit /b 1
