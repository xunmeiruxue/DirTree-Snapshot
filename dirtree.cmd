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
if not exist "%~dp0dirtree_compare.py" goto missing_compare_script
if not exist "%~dp0dirtree_verify.py" goto missing_verify_script
if not exist "%~dp0dirtree_cache.py" goto missing_cache_script
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
echo Python executable: %python_executable%
"%python_executable%" --version 2>&1
"%python_executable%" -u "%~dp0dirtree.py" %* 2>&1
set "exit_code=%errorlevel%"
if "%exit_code%"=="1" echo [NOTICE] Completed with differences or warnings.
if not "%exit_code%"=="0" if not "%exit_code%"=="1" echo [ERROR] dirtree.py exited with code %exit_code%.
goto finish

:working_directory_error
echo.
echo [ERROR] Could not use the launcher directory:
echo %~dp0
set "exit_code=3"
goto finish

:missing_script
echo.
echo [ERROR] Required file was not found:
echo %~dp0dirtree.py
set "exit_code=2"
goto finish

:missing_compare_script
echo.
echo [ERROR] Required comparison module was not found:
echo %~dp0dirtree_compare.py
set "exit_code=2"
goto finish

:missing_verify_script
echo.
echo [ERROR] Required verification module was not found:
echo %~dp0dirtree_verify.py
set "exit_code=2"
goto finish

:missing_cache_script
echo.
echo [ERROR] Required hash cache module was not found:
echo %~dp0dirtree_cache.py
set "exit_code=2"
goto finish

:missing_assets
echo.
echo [ERROR] Required template assets were not found:
echo %~dp0dirtree_assets
echo Keep the complete dirtree_assets folder beside dirtree.py.
set "exit_code=2"
goto finish

:missing_python
echo.
echo [ERROR] Python 3.9 or newer could not be found.
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
