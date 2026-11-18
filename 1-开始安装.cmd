@echo off
cd /d "%~dp0"
set "P2Z=%~dp0scripts"
if exist "%~dp0skill\paper2zh\scripts\bootstrap.py" set "P2Z=%~dp0skill\paper2zh\scripts"
"%~dp0runtime\python\python.exe" "%P2Z%\bootstrap.py" %*
set "RC=%ERRORLEVEL%"
if not defined PAPER2ZH_NO_PAUSE if "%~1"=="" pause
exit /b %RC%