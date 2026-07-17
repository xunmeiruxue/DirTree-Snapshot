@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "pause_on_exit="
set "interactive_mode="
set "interactive_compare="
set "hash_argument="
set "compare_include="
set "python_executable="

if not "%~1"=="" goto use_arguments

set "pause_on_exit=1"
set "interactive_mode=1"
cd /d "%~dp0"
if errorlevel 1 goto working_directory_error
echo DirTree Snapshot
echo.
echo [1] Generate snapshot
echo [2] Compare two snapshots
set "action_choice="
set /p "action_choice=Choose action (1/2, default 1): "
if /I "%action_choice%"=="2" goto interactive_compare_setup
goto interactive_snapshot_setup

:interactive_snapshot_setup
set /p "scan_directory=Directory to scan: "
if not defined scan_directory goto no_directory
set "scan_directory=%scan_directory:"=%"
if "%scan_directory:~-1%"=="\" set "scan_directory=%scan_directory%."
set "hash_choice="
set /p "hash_choice=Calculate SHA-256 hashes? (y/N): "
if /I "%hash_choice%"=="y" set "hash_argument=--hash"
if /I "%hash_choice%"=="yes" set "hash_argument=--hash"
goto check_python

:interactive_compare_setup
set "interactive_compare=1"
set /p "compare_left=Left snapshot file: "
if not defined compare_left goto no_compare_input
set "compare_left=%compare_left:"=%"
set /p "compare_right=Right snapshot file: "
if not defined compare_right goto no_compare_input
set "compare_right=%compare_right:"=%"
set "compare_output="
set /p "compare_output=Output file (Enter for automatic name): "
if not defined compare_output goto compare_output_ready
set "compare_output=%compare_output:"=%"

:compare_output_ready
set "compare_include="
set "compare_choice="
set /p "compare_choice=Include unchanged items? (y/N): "
if /I "%compare_choice%"=="y" set "compare_include=--include-unchanged"
if /I "%compare_choice%"=="yes" set "compare_include=--include-unchanged"
goto check_python

:use_arguments
set "scan_arguments=%*"
goto check_python

:no_directory
echo.
echo Error: no directory was provided.
set "exit_code=2"
goto finish

:no_compare_input
echo.
echo Error: both snapshot files are required.
set "exit_code=2"
goto finish

:check_python
if not exist "%~dp0dirtree.py" goto missing_script
if defined interactive_compare if not exist "%~dp0dirtree_compare.py" goto missing_compare_script

for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do if not defined python_executable set "python_executable=%%P"
if not defined python_executable goto detect_python_command
"%python_executable%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto detect_python_command
goto run_detected_python

:detect_python_command
set "python_executable="
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do if not defined python_executable set "python_executable=%%P"
if not defined python_executable goto missing_python
"%python_executable%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
goto run_detected_python

:run_detected_python
echo Python executable: %python_executable%
"%python_executable%" --version 2>&1
if defined interactive_compare goto run_interactive_compare
if defined interactive_mode goto run_interactive_snapshot
"%python_executable%" -u "%~dp0dirtree.py" %scan_arguments% 2>&1
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" echo [ERROR] dirtree.py exited with code %exit_code%.
goto finish

:run_interactive_snapshot
if defined hash_argument echo SHA-256 hashing: enabled
if not defined hash_argument echo SHA-256 hashing: disabled
"%python_executable%" -u "%~dp0dirtree.py" "%scan_directory%" %hash_argument% 2>&1
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" echo [ERROR] dirtree.py exited with code %exit_code%.
goto finish

:run_interactive_compare
echo Comparing snapshots...
if defined compare_output goto run_compare_with_output
"%python_executable%" -u "%~dp0dirtree.py" compare "%compare_left%" "%compare_right%" %compare_include% 2>&1
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" echo [ERROR] comparison exited with code %exit_code%.
goto finish

:run_compare_with_output
"%python_executable%" -u "%~dp0dirtree.py" compare "%compare_left%" "%compare_right%" -o "%compare_output%" %compare_include% 2>&1
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" echo [ERROR] comparison exited with code %exit_code%.
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
