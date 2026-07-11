@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "pause_on_exit="
set "interactive_mode="
set "hash_argument="

if not "%~1"=="" goto use_arguments

set "pause_on_exit=1"
set "interactive_mode=1"
echo DirTree Snapshot
echo.
set /p "scan_directory=Directory to scan: "
if not defined scan_directory goto no_directory
set "scan_directory=%scan_directory:"=%"
if "%scan_directory:~-1%"=="\" set "scan_directory=%scan_directory%."
set "hash_choice="
set /p "hash_choice=Calculate SHA-256 hashes? (y/N): "
if /I "%hash_choice%"=="y" set "hash_argument=--hash"
if /I "%hash_choice%"=="yes" set "hash_argument=--hash"
goto check_python

:use_arguments
set "scan_arguments=%*"
goto check_python

:no_directory
echo.
echo Error: no directory was provided.
set "exit_code=2"
goto finish

:check_python
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto use_python
goto run_py

:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys" >nul 2>nul
if errorlevel 1 goto missing_python
goto run_python

:run_py
if defined interactive_mode goto run_py_interactive
py -3 "%~dp0dirtree.py" %scan_arguments%
set "exit_code=%errorlevel%"
goto finish

:run_py_interactive
py -3 "%~dp0dirtree.py" "%scan_directory%" %hash_argument%
set "exit_code=%errorlevel%"
goto finish

:run_python
if defined interactive_mode goto run_python_interactive
python "%~dp0dirtree.py" %scan_arguments%
set "exit_code=%errorlevel%"
goto finish

:run_python_interactive
python "%~dp0dirtree.py" "%scan_directory%" %hash_argument%
set "exit_code=%errorlevel%"
goto finish

:missing_python
echo.
echo [ERROR] Python 3.9 or newer could not be started.
echo Install Python from https://www.python.org/downloads/windows/
echo Then open a new CMD and run: py -3 --version
set "exit_code=9009"

goto finish

:finish
if defined pause_on_exit (
    echo.
    pause
)
endlocal & exit /b %exit_code%
