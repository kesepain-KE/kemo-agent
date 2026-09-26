@echo off
python "%~dp0deploy.py" %*
exit /b %errorlevel%

