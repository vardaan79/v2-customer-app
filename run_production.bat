@echo off
REM V2 Retail Customer App — production launcher (waitress).
REM For the dev server with live reload, use run_app.bat instead.
cd /d "%~dp0"
python serve.py %*
pause
