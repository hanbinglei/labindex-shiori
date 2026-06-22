@echo off
title LabIndex Shiori -- Parse & Re-Index Tool
cd /d "%~dp0"

echo =============================================
echo    LabIndex Shiori  -  Parse  /  Re-Index
echo =============================================
echo(

set MEILI_PORT=7700

REM --- Check venv ---
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found.
    pause
    exit /b 1
)

echo  Choose mode:
echo(
echo   1 - Incremental parse  (fast, new files only)
echo   2 - Full re-parse      (--force, redo all files)
echo(
choice /c 12 /n /m "Press 1 or 2: "

if errorlevel 2 (
    set PARSE_FLAG=--force
    set INDEX_FLAG=--full
    echo   [OK] Full re-parse -- all files will be re-processed.
) else (
    set PARSE_FLAG=
    set INDEX_FLAG=
    echo   [OK] Incremental parse -- only new/changed files.
)
echo(

REM --- Step 1: Parse ---
echo [1/3] Parsing file metadata...
.venv\Scripts\python.exe -m src.main parse %PARSE_FLAG%
if errorlevel 1 (
    echo   [!] Parse had errors.
    pause
    exit /b 1
)
echo   [OK] Parse complete.
echo(

REM --- Step 2: Relation ---
echo [2/3] Recalculating research relations (M10)...
.venv\Scripts\python.exe -m src.main relation
if errorlevel 1 (
    echo   [!] Relation calculation had errors.
)
echo   [OK] Relations updated.
echo(

REM --- Step 3: Index ---
echo [3/3] Building search index...

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
        :wait_meili_parse
        timeout /t 1 /nobreak >nul
        set /a WAIT+=1
        curl -sf "http://127.0.0.1:%MEILI_PORT%/health" >nul 2>&1
        if not errorlevel 1 goto meili_ok_parse
        if %WAIT% lss 15 goto wait_meili_parse
        echo   [!] Meilisearch startup timed out.
        goto meili_done_parse
        :meili_ok_parse
        echo   [OK] Meilisearch ready.
        :meili_done_parse
    )
)

.venv\Scripts\python.exe -m src.main index %INDEX_FLAG%
if errorlevel 1 (
    echo   [!] Index had errors. Is Meilisearch running?
    echo       Run Start.bat first, then re-run.
)
echo   [OK] Index complete.
echo(

echo =============================================
echo    Done! Refresh browser to see changes.
echo =============================================
echo(
pause
exit /b 0