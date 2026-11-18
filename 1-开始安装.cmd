@echo off
cd /d "%~dp0"
set "P2Z=%~dp0scripts"
if exist "%~dp0skill\paper2zh\scripts\bootstrap.py" set "P2Z=%~dp0skill\paper2zh\scripts"
"%~dp0runtime\python\python.exe" "%P2Z%\bootstrap.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done
if not "%~1"=="" goto done
echo.
echo [5/5] ?? AI ??...
"%~dp0runtime\python\python.exe" "%P2Z%\install_skill.py" --pack "%~dp0paper2zh-skill.zip"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done
echo.
echo [6/6] ??...
"%~dp0runtime\python\python.exe" "%P2Z%\doctor.py" --quick
set "RC=%ERRORLEVEL%"
:done
if not defined PAPER2ZH_NO_PAUSE if "%~1"=="" pause
exit /b %RC%