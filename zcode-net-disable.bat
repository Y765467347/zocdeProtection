@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo WARNING: this FULLY REMOVES the network audit/enforcement layers.
echo Only run this to deliberately roll back - NOT to fix ZCode errors.
echo (For ZCode network errors, restart ZCode from its shortcut instead.)
choice /C YN /T 30 /D N /M "Really remove network enforcement? [Y/N]"
if not %errorlevel%==1 exit /b

echo [1/5] Removing firewall rule...
netsh advfirewall firewall delete rule name="ZCodeForceLoopbackProxy" >nul 2>&1
netsh advfirewall firewall delete rule name="ZCodeForceLoopbackProxy-user" >nul 2>&1

echo [2/5] Removing proxy env vars...
setx HTTP_PROXY "" >nul
setx HTTPS_PROXY "" >nul
setx NO_PROXY "" >nul
setx NODE_EXTRA_CA_CERTS "" >nul

echo [2b/5] Removing httpProxy keys from setting.json...
if exist "%USERPROFILE%\.zcode2\setting.json" python -c "import os,json; f=os.path.join(os.environ['USERPROFILE'],'.zcode','v2','setting.json'); j=json.load(open(f,encoding='utf-8-sig')); [j.pop(k,None) for k in ('httpProxy','httpProxyNoProxy','httpProxyCaCertPath')]; json.dump(j,open(f,'w',encoding='utf-8'),indent=2,ensure_ascii=False)" 2>nul
echo [3/5] Un-patching ZCode shortcuts...
powershell -NoProfile -Command "$sh = New-Object -ComObject WScript.Shell; $dirs = @(\"$env:USERPROFILE\Desktop\", \"$env:APPDATA\Microsoft\Windows\Start Menu\", \"$env:ProgramData\Microsoft\Windows\Start Menu\"); Get-ChildItem $dirs -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $l = $sh.CreateShortcut($_.FullName); if ($l.TargetPath -like '*ZCode.exe' -and $l.Arguments -like '*proxy-server*') { $l.Arguments = ($l.Arguments -replace ' --proxy-server=http://127\.0\.0\.1:876[56]', '').Trim(); $l.Save(); Write-Output ('  restored: ' + $_.FullName) } }"

echo [4/5] Stopping proxy processes and removing tasks...
powershell -NoProfile -Command "Get-Process mitmdump -ErrorAction SilentlyContinue | Stop-Process -Force"
schtasks /Delete /TN "ZCodeNetAudit" /F >nul 2>&1
schtasks /Delete /TN "ZCodeNetMITM" /F >nul 2>&1
python -c "import psutil
for p in psutil.process_iter(['name','cmdline']):
    try:
        if (p.info['name'] or '')=='pythonw.exe' and any('net_audit_proxy' in c for c in (p.info['cmdline'] or [])):
            p.kill()
    except Exception: pass" 2>nul

echo [5/5] Optional: remove the MITM root CA from the system store...
echo   run manually if it was installed:  certutil -delstore Root mitmproxy
echo.
echo Rollback complete. ZCode connects directly again (snapshot defenses
echo from zcode-kill-snapshot-upload.bat are NOT affected by this script).
pause
