@echo off
cd /d "%~dp0"
set "P2Z=%~dp0scripts"
if exist "%~dp0skill\pdf2zh\scripts\install_skill.py" set "P2Z=%~dp0skill\pdf2zh\scripts"
"%~dp0runtime\python\python.exe" "%P2Z%\install_skill.py" --pack "%~dp0pdf2zh-skill.zip" %*
set "RC=%ERRORLEVEL%"
if not defined PDF2ZH_NO_PAUSE if "%~1"=="" pause
exit /b %RC%