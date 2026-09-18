@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "TOOLS=%USERPROFILE%\.zcode-tools"
set "CKPT=%USERPROFILE%\.zcode\v2\checkpoints"
set "SETTING=%USERPROFILE%\.zcode\v2\setting.json"

echo ==========================================================
echo  DEFINITIVE: remove snapshot-upload at the source + relayer
echo ==========================================================

echo [1/9] Locating Python (skipping Microsoft Store stub)...
set "PY="
for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i /c:"WindowsApps" >nul
    if errorlevel 1 if not defined PY set "PY=%%i"
)
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo [ABORT] Python not found. Install Python 3 from python.org and tick "Add to PATH".
    pause
    exit /b 1
)
set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    echo %%i | findstr /i /c:"WindowsApps" >nul
    if errorlevel 1 if not defined PYW set "PYW=%%i"
)
if not defined PYW set "PYW=%PY%"
echo       python: %PY%

echo [2/9] Installing tool files to a fixed location: %TOOLS%
if not exist "%TOOLS%" mkdir "%TOOLS%"
if exist "%~dp0tools\snapshot_guard.py" copy /Y "%~dp0tools\snapshot_guard.py" "%TOOLS%\" >nul
if exist "%~dp0tools\patch_asar.py"    copy /Y "%~dp0tools\patch_asar.py" "%TOOLS%\" >nul
if exist "%~dp0tools\guard_resume.py"  copy /Y "%~dp0tools\guard_resume.py" "%TOOLS%\" >nul
if exist "%~dp0tools\watchdog.ps1"     copy /Y "%~dp0tools\watchdog.ps1" "%TOOLS%\" >nul
if exist "%~dp0zcode-guard-resume.bat" copy /Y "%~dp0zcode-guard-resume.bat" "%TOOLS%\" >nul
copy /Y "%~f0" "%TOOLS%\zcode-kill-snapshot-upload.bat" >nul

echo [3/9] Ensuring psutil...
%PY% -c "import psutil" 2>nul || %PY% -m pip install --user psutil

echo [4/9] Closing ZCode completely...
taskkill /IM ZCode.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul

echo [5/9] Patching app.asar (upload-credential endpoint -^> 404)...
%PY% "%TOOLS%\patch_asar.py"
if not %errorlevel%==0 (
    echo [ABORT] patch failed - nothing else was changed. ZCode keeps working.
    pause
    exit /b 1
)

echo [6/9] Clearing and locking the checkpoints folder...
if exist "%CKPT%" rd /s /q "%CKPT%"
if exist "%CKPT%" (
    echo [WARN] could not fully delete - continuing with ACL lock anyway
) else (
    mkdir "%CKPT%"
)
icacls "%CKPT%" /deny "%USERNAME%:(OI)(CI)(WD,AD,DC)"

echo [7/9] Hardening settings (snapshot indexing / experience upload /
echo       auto-update / preview channel OFF)...
if exist "%SETTING%" (
    if not exist "%SETTING%.bak-before-block" copy /Y "%SETTING%" "%SETTING%.bak-before-block" >nul
    set "SETTING=%SETTING%"
    %PY% -c "import os,json; f=os.environ['SETTING']; j=json.load(open(f,encoding='utf-8-sig')); j['repoSnapshotIndexingEnabled']=False; j['optimizeAgentExperienceEnabled']=False; j['autoDownloadAndInstallUpdates']=False; j['receivePreviewUpdates']=False; json.dump(j,open(f,'w',encoding='utf-8'),indent=2)"
    echo       done (backup: setting.json.bak-before-block)
)

echo [8/9] Registering guard + watchdog tasks (guard starts elevated)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'snapshot_guard' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
schtasks /Create /TN "ZCodeSnapshotGuard" /SC ONLOGON /DELAY 0000:30 /RL HIGHEST /F /TR "\"%PYW%\" \"%TOOLS%\snapshot_guard.py\""
schtasks /Create /TN "ZCodeSnapshotWatchdog" /SC MINUTE /MO 15 /F /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%TOOLS%\watchdog.ps1\""
schtasks /Run /TN "ZCodeSnapshotGuard"

echo [9/9] Verification summary...
echo --- endpoint inside app.asar (want: endpoint=0) ---
%PY% "%TOOLS%\patch_asar.py" --check
echo --- checkpoints folder (want: empty + DENY line) ---
dir /b "%CKPT%" 2>nul
icacls "%CKPT%" | findstr /i "deny"
echo.
echo DONE - defense stack:
echo   L1  app.asar patch   : upload credential unobtainable, capture aborts
echo   L2  ACL deny         : no local packaging even if L1 is bypassed
echo   L3  real-time guard  : suffix+content+entropy detection, freeze+delete
echo   L4  15-min watchdog  : cleanup/audit trail
echo   Tools installed at  : %TOOLS%
echo   Logs                : %TOOLS%\guard.log / %TOOLS%\watchdog.log
echo Start ZCode normally. Checkpoint/timeline rollback is the only lost feature.
echo Full rollback: zcode-restore-original.bat
pause
