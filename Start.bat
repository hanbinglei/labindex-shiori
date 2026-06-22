@echo off
title LabIndex Shiori -- Launcher
cd /d "%~dp0"

set MEILI_PORT=7700
set WEB_PORT=5000

echo =============================================
echo    LabIndex Shiori  -  Launcher
echo =============================================
echo/

if exist ".venv\Scripts\python.exe" goto step1
echo [ERROR] Virtual environment .venv not found.
echo         Run: python -m venv .venv
echo         Then: .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1

:step1
echo [1/4] Starting Meilisearch engine...

if not exist "meilisearch.exe" goto no_meili

netstat -an 2>nul | findstr "127.0.0.1:%MEILI_PORT%" >nul 2>&1
if not errorlevel 1 goto meili_skip

set MEILI_KEY=%MEILI_MASTER_KEY%
if defined MEILI_KEY goto meili_launch

for /f "tokens=2 delims=: " %%a in ('findstr "api_key:" config.yaml') do set MEILI_KEY=%%a

:meili_launch
start "Meili" /B .\meilisearch.exe --master-key "%MEILI_KEY%" --http-addr "127.0.0.1:%MEILI_PORT%" > nul 2>&1

set WAIT=0
:wait_meili
timeout /t 1 /nobreak >nul
set /a WAIT+=1
curl -sf "http://127.0.0.1:%MEILI_PORT%/health" >nul 2>&1
if not errorlevel 1 goto meili_ok
if %WAIT% lss 15 goto wait_meili
echo   [!] Meilisearch startup timed out.
goto meili_done

:meili_ok
echo   [OK] Meilisearch ready.
goto meili_done

:meili_skip
echo   [SKIP] Port %MEILI_PORT% already in use.
goto meili_done

:no_meili
echo   [!] meilisearch.exe not found. Search unavailable.
echo       Still launch Web UI? (C=Continue / N=Cancel)
choice /c CN /n
if errorlevel 2 exit /b 1

:meili_done
echo/

echo [2/4] Starting Web UI...
set PYTHONIOENCODING=utf-8
start "LabIndex" /B .venv\Scripts\python.exe -m src.main serve

set WAIT=0
:wait_web
timeout /t 1 /nobreak >nul
set /a WAIT+=1
curl -sf "http://127.0.0.1:%WEB_PORT%/" >nul 2>&1
if not errorlevel 1 goto web_ok
if %WAIT% lss 15 goto wait_web
echo   [!] Web UI startup timed out.
goto web_done

:web_ok
echo   [OK] Web UI ready.

:web_done
echo/

echo [3/4] Opening browser...
start http://127.0.0.1:%WEB_PORT%
echo   [OK] Browser opened.
echo/

echo [4/4] All services running.
echo/
echo =============================================
echo    LabIndex Shiori  is running
echo/
echo    URL: http://127.0.0.1:%WEB_PORT%
echo/
echo    Close this window to stop all services.
echo =============================================
echo/
echo Type 0 and press Enter to stop.

:wait_loop
set /p input="> "
if "%input%"=="0" goto cleanup
goto wait_loop

:cleanup
echo/
echo Stopping services...
taskkill /f /im meilisearch.exe >nul 2>&1
echo [OK] Stopped.
exit /b 0
