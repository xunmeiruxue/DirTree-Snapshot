@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "pause_on_exit="
set "python_executable="

if not "%~1"=="" goto check_files
set "pause_on_exit=1"
cd /d "%~dp0"
if errorlevel 1 goto working_directory_error

:check_files
if not exist "%~dp0dirtree.py" goto missing_script
if not exist "%~dp0dirtree_gui.py" goto missing_gui_script
if not exist "%~dp0dirtree_assets\__init__.py" goto missing_assets

for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do if not defined python_executable set "python_executable=%%P"
if not defined python_executable goto detect_python_command
"%python_executable%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto detect_python_command
goto run_python

:detect_python_command
set "python_executable="
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do if not defined python_executable set "python_executable=%%P"
if not defined python_executable goto missing_python
"%python_executable%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python

:run_python
"%python_executable%" -u "%~dp0dirtree_gui.py" %*
set "exit_code=%errorlevel%"
goto finish

:missing_script
echo [ERROR] dirtree.py not found next to dirtree-gui.cmd.
goto finish

:missing_gui_script
echo [ERROR] dirtree_gui.py not found next to dirtree-gui.cmd.
goto finish

:missing_assets
echo [ERROR] dirtree_assets\__init__.py not found. The assets folder must sit next to the scripts.
goto finish

:missing_python
echo [ERROR] Python 3.9+ was not found. Install it from https://www.python.org/downloads/
goto finish

:working_directory_error
echo [ERROR] Could not switch to the script directory.
goto finish

:finish
if "%pause_on_exit%"=="1" pause
endlocal & exit /b %exit_code%
