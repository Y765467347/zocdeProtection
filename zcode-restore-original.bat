@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "ASAR_DIR=C:\Program Files\ZCode\resources"
set "CKPT=%USERPROFILE%\.zcode\v2\checkpoints"
set "TOOLS=%USERPROFILE%\.zcode-tools"

echo [1/5] Closing ZCode...
taskkill /IM ZCode.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/5] Restoring original app.asar...
if exist "%ASAR_DIR%\app.asar.original-backup" (
    copy /Y "%ASAR_DIR%\app.asar.original-backup" "%ASAR_DIR%\app.asar"
    echo       restored from %ASAR_DIR%\app.asar.original-backup
) else if exist "%LOCALAPPDATA%\Programs\ZCode\resources\app.asar.original-backup" (
    copy /Y "%LOCALAPPDATA%\Programs\ZCode\resources\app.asar.original-backup" "%LOCALAPPDATA%\Programs\ZCode\resources\app.asar"
    echo       restored from per-user install location
) else (
    echo       no backup found - app.asar untouched
)

echo [3/5] Stopping guard and removing scheduled tasks...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'snapshot_guard' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
schtasks /Delete /TN "ZCodeSnapshotGuard" /F >nul 2>&1
schtasks /Delete /TN "ZCodeSnapshotWatchdog" /F >nul 2>&1
echo       done

echo [4/5] Removing ACL deny on checkpoints...
if exist "%CKPT%" icacls "%CKPT%" /remove:d "%USERNAME%" >nul

echo [5/5] Settings: re-enable auto-update etc. in ZCode Settings UI if
echo       wanted, or restore the backup:
echo       %USERPROFILE%\.zcode\v2\setting.json.bak-before-block
echo       (tools and logs kept at %TOOLS% - delete manually if desired)
echo.
echo Rollback complete. Start ZCode normally - snapshot behavior is back
echo to stock (including its upload pipeline).
pause
