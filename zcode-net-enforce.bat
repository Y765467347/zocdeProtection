@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "TOOLS=%USERPROFILE%\.zcode-tools"
set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python312"
set "PYW=%PYDIR%\pythonw.exe"
set "MITMDUMP=%PYDIR%\Scripts\mitmdump.exe"

echo ==========================================================
echo  Network enforcement: forced loopback + audit layers
echo ==========================================================

if not exist "%PYW%" (
    echo [ABORT] pythonw not found at %PYW%
    pause
    exit /b 1
)
if not exist "%TOOLS%\net_audit_proxy.py" (
    echo [ABORT] %TOOLS%\net_audit_proxy.py missing - run zcode-kill-snapshot-upload.bat first
    pause
    exit /b 1
)

choice /C YN /T 30 /D N /M "Enable MITM content inspection? [Y/N] (auto-N in 30s)"
if %errorlevel%==1 (set "MODE=mitm" & set "PROXYPORT=8766") else (set "MODE=basic" & set "PROXYPORT=8765")

echo [1/6] Closing ZCode (config applies to new processes only)...
taskkill /IM ZCode.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/6] Registering + starting the counting proxy (port 8765)...
schtasks /Create /TN "ZCodeNetAudit" /SC ONLOGON /DELAY 0000:30 /F /TR "\"%PYW%\" \"%TOOLS%\net_audit_proxy.py\""
schtasks /Run /TN "ZCodeNetAudit"

if "%MODE%"=="mitm" (
    echo [3/6] Starting MITM inspection layer (port 8766 -chained- 8765)...
    if not exist "%MITMDUMP%" (
        echo   mitmdump missing - installing mitmproxy...
        "%PYDIR%\python.exe" -m pip install --user mitmproxy >nul 2>&1
    )
    rem generate a launcher bat - nested quoting inside schtasks /TR is unreliable
    > "%TOOLS%\run_mitm.bat" echo @echo off
    >> "%TOOLS%\run_mitm.bat" echo "%MITMDUMP%" -s "%TOOLS%\content_addon.py" --listen-host 127.0.0.1 --listen-port 8766 --mode upstream:http://127.0.0.1:8765 --set "confdir=%USERPROFILE%\.mitmproxy" >> "%TOOLS%\mitmdump_err.log" 2^>^&1
    schtasks /Create /TN "ZCodeNetMITM" /SC ONLOGON /DELAY 0000:35 /F /TR "\"%TOOLS%\run_mitm.bat\""
    schtasks /Run /TN "ZCodeNetMITM"
    timeout /t 8 /nobreak >nul
    certutil -addstore -f Root "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
    setx NODE_EXTRA_CA_CERTS "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem" >nul
) else (
    echo [3/6] MITM layer skipped (basic counting mode).
)

echo [4/6] Pointing user traffic at the proxy (%PROXYPORT%)...
setx HTTP_PROXY "http://127.0.0.1:%PROXYPORT%" >nul
setx HTTPS_PROXY "http://127.0.0.1:%PROXYPORT%" >nul
setx NO_PROXY "localhost,127.0.0.1" >nul

echo [4b/6] Pointing ZCode API client at the proxy (official setting.json keys)...
if exist "%USERPROFILE%\.zcode2\setting.json" "%PYDIR%\python.exe" -c "import os,json; f=os.path.join(os.environ['USERPROFILE'],'.zcode','v2','setting.json'); j=json.load(open(f,encoding='utf-8-sig')); j['httpProxy']='http://127.0.0.1:%PROXYPORT%'; j['httpProxyNoProxy']='localhost,127.0.0.1'; j['httpProxyCaCertPath']=os.path.join(os.environ['USERPROFILE'],'.mitmproxy','mitmproxy-ca-cert.pem'); json.dump(j,open(f,'w',encoding='utf-8'),indent=2,ensure_ascii=False)" 2>nul && echo       httpProxy keys written
echo [5/6] Patching ZCode shortcuts with --proxy-server...
powershell -NoProfile -Command "$sh = New-Object -ComObject WScript.Shell; $dirs = @(\"$env:USERPROFILE\Desktop\", \"$env:APPDATA\Microsoft\Windows\Start Menu\", \"$env:ProgramData\Microsoft\Windows\Start Menu\"); Get-ChildItem $dirs -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $l = $sh.CreateShortcut($_.FullName); if ($l.TargetPath -like '*ZCode.exe' -and $l.Arguments -notlike '*proxy-server*') { $l.Arguments = ($l.Arguments + ' --proxy-server=http://127.0.0.1:%PROXYPORT%').Trim(); $l.Save(); Write-Output ('  patched: ' + $_.FullName) } }"

echo [6/6] Firewall: block ZCode.exe direct outbound (loopback stays open)...
netsh advfirewall firewall delete rule name="ZCodeForceLoopbackProxy" >nul 2>&1
if exist "C:\Program Files\ZCode\ZCode.exe" netsh advfirewall firewall add rule name="ZCodeForceLoopbackProxy" dir=out program="C:\Program Files\ZCode\ZCode.exe" action=block
if exist "%LOCALAPPDATA%\Programs\ZCode\ZCode.exe" netsh advfirewall firewall add rule name="ZCodeForceLoopbackProxy-user" dir=out program="%LOCALAPPDATA%\Programs\ZCode\ZCode.exe" action=block

echo.
echo DONE. Mode=%MODE%  proxy port %PROXYPORT%
echo   - start ZCode from its shortcut; check activity with:
echo     python "%TOOLS%\net_audit_proxy.py" --stats
echo   - content log (mitm mode): %TOOLS%\net_content.log
echo   - byte alerts: popup + %TOOLS%\net_audit.log
echo   - rollback: zcode-net-disable.bat
echo NOTE: if the proxy is stopped, ZCode has NO network (fail-closed by
echo       design). Watchdog restarts it at most 15 min late.
pause
