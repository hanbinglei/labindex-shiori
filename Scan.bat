@echo off
title LabIndex Shiori -- Scan Tool
cd /d "%~dp0"

echo =============================================
echo    LabIndex Shiori  -  Scan Tool
echo =============================================
echo(

set MEILI_PORT=7700

REM --- Check venv ---
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found.
    echo         Run: .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM --- Step 1: Folder selection ---
REM Uses standalone pick_folder.ps1 (no dynamic code generation).
REM InputBox for typing/pasting, or leave empty for graphical picker.
echo [1/3] Select a folder to scan...
echo(

if not exist "pick_folder.ps1" (
    echo [ERROR] pick_folder.ps1 not found next to this script.
    pause
    exit /b 1
)

set "SCAN_ROOT="
for /f "delims=" %%a in ('powershell -NoProfile -NoLogo -ExecutionPolicy Bypass -STA -File "%~dp0pick_folder.ps1"') do set "SCAN_ROOT=%%a"

if not defined SCAN_ROOT (
    echo(
    echo   Cancelled.
    pause
    exit /b 0
)

REM Basic sanity: path must contain a backslash (drive or UNC)
echo %SCAN_ROOT% | findstr "\\" >nul
if errorlevel 1 (
    echo [ERROR] Invalid path: %SCAN_ROOT%
    pause
    exit /b 1
)
echo   [OK] Selected: %SCAN_ROOT%
echo   [INFO] Will scan this folder recursively (all subfolders included).
echo(

REM --- Step 2: Scan mode ---
echo [2/3] Select scan mode
echo(
echo   1 - Incremental (fast)  -- only new/changed files, for daily use
echo   2 - Full (slow)         -- rescan everything, for first use or data issues
echo(
choice /c 12 /n /m "Press 1 or 2: "
if errorlevel 2 (
    set SCAN_FLAG=--full
    set INDEX_FLAG=--full
    set PARSE_FLAG=--force
    echo   [OK] Full scan
) else (
    set SCAN_FLAG=--incremental
    set INDEX_FLAG=
    set PARSE_FLAG=
    echo   [OK] Incremental scan
)
echo(

REM --- Step 3: Run pipeline ---
echo [3/3] Starting pipeline...
echo   [INFO] Do not close this window while scanning.
echo(

REM --- Scan ---
echo [1/3] Scanning files...
.venv\Scripts\python.exe -m src.main scan --root "%SCAN_ROOT%" %SCAN_FLAG%

if errorlevel 1 (
    echo(
    echo [ERROR] Scan failed. Common causes:
    echo    - NAS is offline or network issue
    echo    - Selected folder does not exist or no permission
    echo    - NAS connections exhausted (wait 15-30 min then retry)
    echo(
    pause
    exit /b 1
)
echo   [OK] Scan complete.
echo(

REM --- Parse ---
echo [2/3] Parsing file metadata...
.venv\Scripts\python.exe -m src.main parse %PARSE_FLAG%
if errorlevel 1 (
    echo   [!] Parse had errors. Some files may not be parsed.
    echo       You can re-run parse separately later.
)
echo   [OK] Parse complete.
echo(

REM --- Index ---
echo [3/3] Building search index (classification + Meilisearch push)...

REM Auto-start Meilisearch if not already running
if exist "meilisearch.exe" (
    netstat -an 2>nul | findstr "127.0.0.1:%MEILI_PORT%" >nul 2>&1
    if errorlevel 1 (
        echo   [INFO] Starting Meilisearch automatically...
        set MEILI_KEY=%MEILI_MASTER_KEY%
        if not defined MEILI_KEY (
            for /f "tokens=2 delims=: " %%a in ('findstr "api_key:" config.yaml') do set MEILI_KEY=%%a
        )
        start "Meili" /B .\meilisearch.exe --master-key "%MEILI_KEY%" --http-addr "127.0.0.1:%MEILI_PORT%" > nul 2>&1
        set WAIT=0
        :wait_meili_scan
        timeout /t 1 /nobreak >nul
        set /a WAIT+=1
        curl -sf "http://127.0.0.1:%MEILI_PORT%/health" >nul 2>&1
        if not errorlevel 1 goto meili_ok_scan
        if %WAIT% lss 15 goto wait_meili_scan
        echo   [!] Meilisearch startup timed out.
        goto meili_done_scan
        :meili_ok_scan
        echo   [OK] Meilisearch ready.
        :meili_done_scan
    )
)

.venv\Scripts\python.exe -m src.main index %INDEX_FLAG%
if errorlevel 1 (
    echo   [!] Index had errors. Meilisearch may not be running.
    echo       Run Start.bat first to start services, then re-run index:
    echo       .venv\Scripts\python.exe -m src.main index
)
echo   [OK] Index complete.
echo(

REM --- Done ---
echo =============================================
echo    Scan complete!
echo(
echo    Refresh browser to see latest data.
echo(
if "%SCAN_FLAG%"=="--full" (
    echo    Full scan is slower, this is normal.
)
echo =============================================
echo(
pause
exit /b 0
