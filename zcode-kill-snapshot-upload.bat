@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PY=C:\Users\76546\AppData\Local\Programs\Python\Python312\python.exe"
set "CKPT=%USERPROFILE%\.zcode\v2\checkpoints"
set "SETTING=%USERPROFILE%\.zcode\v2\setting.json"
set "TOOLS=%USERPROFILE%\.zcode-tools"

echo ==========================================================
echo  DEFINITIVE: remove snapshot-upload at the source + relayer
echo ==========================================================
echo [1/7] Closing ZCode completely...
taskkill /IM ZCode.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/7] Patching app.asar (upload-credential endpoint -^> 404)...
"%PY%" "%TOOLS%\patch_asar.py"
if not %errorlevel%==0 (
    echo [ABORT] patch failed - nothing else was changed. ZCode keeps working.
    pause
    exit /b 1
)

echo [3/7] Clearing and locking the checkpoints folder...
if exist "%CKPT%" rd /s /q "%CKPT%"
if exist "%CKPT%" (
    echo [WARN] could not fully delete - continuing with ACL lock anyway
) else (
    mkdir "%CKPT%"
)
icacls "%CKPT%" /deny "%USERNAME%:(OI)(CI)(WD,AD,DC)"

echo [4/7] Hardening settings (snapshot indexing / experience upload /
echo       auto-update / preview channel OFF)...
if exist "%SETTING%" (
    if not exist "%SETTING%.bak-before-block" copy /Y "%SETTING%" "%SETTING%.bak-before-block" >nul
    "%PY%" -c "import os,json; f=os.environ['SETTING']; j=json.load(open(f,encoding='utf-8-sig')); j['repoSnapshotIndexingEnabled']=False; j['optimizeAgentExperienceEnabled']=False; j['autoDownloadAndInstallUpdates']=False; j['receivePreviewUpdates']=False; json.dump(j,open(f,'w',encoding='utf-8'),indent=2)"
    echo       done (backup: setting.json.bak-before-block)
)

echo [5/7] Re-enabling the FIXED real-time guard (elevated)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'snapshot_guard' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
schtasks /Change /TN "ZCodeSnapshotGuard" /ENABLE
schtasks /Change /TN "ZCodeSnapshotGuard" /RL HIGHEST
schtasks /Run /TN "ZCodeSnapshotGuard"

echo [6/7] Watchdog (15-min cleanup layer) stays registered as-is.

echo [7/7] Verification summary...
echo --- endpoint inside app.asar (want: endpoint=0) ---
"%PY%" "%TOOLS%\patch_asar.py" --check
echo --- checkpoints folder (want: empty + DENY line) ---
dir /b "%CKPT%" 2>nul
icacls "%CKPT%" | findstr /i "deny"
echo.
echo DONE - defense stack now:
echo   L1  app.asar patch   : upload credential unobtainable, capture aborts
echo   L2  ACL deny         : no local packaging even if L1 is bypassed
echo   L3  real-time guard  : freezes^+deletes if an .enc ever appears (fixed build)
echo   L4  15-min watchdog  : cleanup/audit trail
echo Start ZCode normally. Checkpoint/timeline rollback is the only lost feature.
echo Full rollback: zcode-restore-original.bat
pause
