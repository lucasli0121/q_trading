@echo off
REM ============================================================
REM q_data_proxy - PyInstaller auto build script
REM
REM Usage : run build.bat in project root (or double-click)
REM Output: dist\q_data_proxy\q_data_proxy.exe
REM
REM Steps :
REM   1. cd to script dir (project root)
REM   2. check .venv virtual environment
REM   3. install PyInstaller if missing
REM   4. compress dist\q_data_proxy\ -> dist\q_data_proxy.zip
REM ============================================================
setlocal

REM cd to script dir so relative paths in main.spec resolve
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"

REM ---------- check venv ----------
if not exist "%PYTHON%" (
    echo [ERROR] venv not found: %PYTHON%
    echo         run: python -m venv .venv, then pip install -r requirements.txt
    exit /b 1
)

REM ---------- ensure PyInstaller ----------
"%PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] PyInstaller not installed, installing...
    "%PYTHON%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed
        exit /b 1
    )
)

REM ---------- build ----------
echo [INFO] building q_data_proxy ...
"%PYTHON%" -m PyInstaller main.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] build failed, see log above
    exit /b 1
)

REM ---------- compress ----------
set "ZIP_FILE=dist\q_data_proxy.zip"
echo.
echo [INFO] compressing to %ZIP_FILE% ...
if exist "%ZIP_FILE%" del /f "%ZIP_FILE%"
REM compress the folder itself (not *) so the zip extracts into q_data_proxy\
powershell -NoProfile -Command "Compress-Archive -Path 'dist\q_data_proxy' -DestinationPath '%ZIP_FILE%'" 2>&1
if errorlevel 1 (
    echo [WARN] compression failed, but build artifacts are intact in dist\q_data_proxy\
    echo [OK] build done: dist\q_data_proxy\q_data_proxy.exe
    exit /b 1
)

for %%F in ("%ZIP_FILE%") do set "ZIP_SIZE=%%~zF"
echo.
echo [OK] build done: dist\q_data_proxy\q_data_proxy.exe
echo [OK]  zip done: %ZIP_FILE% (%ZIP_SIZE% bytes)
endlocal
exit /b 0
