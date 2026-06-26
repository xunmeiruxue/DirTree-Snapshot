@echo off
setlocal

set "pause_on_exit="
if "%~1"=="" set "pause_on_exit=1"

where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto use_python

py -3 "%~dp0dirtree.py" %*
set "exit_code=%errorlevel%"
goto finish

:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys" >nul 2>nul
if errorlevel 1 goto missing_python

python "%~dp0dirtree.py" %*
set "exit_code=%errorlevel%"
goto finish

:missing_python
echo Error: Python 3 was not found. Install it from https://www.python.org/downloads/windows/
set "exit_code=9009"

:finish
if defined pause_on_exit (
    echo.
    pause
)
endlocal & exit /b %exit_code%
