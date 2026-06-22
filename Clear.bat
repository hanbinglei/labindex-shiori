@echo off
title LabIndex Shiori -- Clear Database
cd /d "%~dp0"

echo =============================================
echo    LabIndex Shiori  -  Clear Database
echo =============================================
echo(
echo  WARNING: This will DELETE auto-generated data only:
echo    - data/labindex.db   (SQLite database)
echo    - data.ms/           (Meilisearch index)
echo(
echo  Manual corrections (overlay, manual edges) will be preserved.
echo(
choice /c YN /n /m "Are you sure? (Y/N): "
if errorlevel 2 (
    echo  Cancelled.
    pause
    exit /b 0
)
echo(

REM --- Stop any running services ---
echo [1/3] Stopping services...
tskill meilisearch 2>nul
tskill python 2>nul
echo  [OK] Stopped.
echo(

REM --- Delete database and index ---
echo [2/3] Deleting data...
if exist "data\labindex.db" del /f /q "data\labindex.db" && echo  [OK] Deleted data/labindex.db
if exist "data\labindex.db-journal" del /f /q "data\labindex.db-journal" 2>nul
if exist "data\labindex.db-wal" del /f /q "data\labindex.db-wal" 2>nul
if exist "data\labindex.db-shm" del /f /q "data\labindex.db-shm" 2>nul
if exist "data.ms" rmdir /s /q "data.ms" && echo  [OK] Deleted data.ms/
echo(

REM --- Recreate empty database ---
echo [3/3] Creating fresh database...
".venv\Scripts\python.exe" -c "from src.database.schema import init_database; init_database('data/labindex.db')" && echo  [OK] Empty database created
echo(

echo =============================================
echo    Done! Database is now empty.
echo(
echo    Next steps:
echo    1. Run 扫描.bat  (Full mode)
echo    2. Run 启动.bat
echo    3. Refresh browser
echo =============================================
echo(
pause
exit /b 0
