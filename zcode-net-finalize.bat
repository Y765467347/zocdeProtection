@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/4] Installing the mitmproxy CA into the system Root store...
certutil -addstore -f Root "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"

echo [2/4] Patching the all-users Start Menu shortcut...
powershell -NoProfile -Command "$sh = New-Object -ComObject WScript.Shell; $p = 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\ZCode.lnk'; if (Test-Path $p) { $l = $sh.CreateShortcut($p); if ($l.Arguments -notlike '*proxy-server*') { $l.Arguments = ($l.Arguments + ' --proxy-server=http://127.0.0.1:8766').Trim(); $l.Save() }; Write-Output ('ARGS=[' + $sh.CreateShortcut($p).Arguments + ']') }"

echo [3/4] Firewall: ZCode.exe direct outbound blocked (loopback stays open)...
netsh advfirewall firewall delete rule name="ZCodeForceLoopbackProxy" >nul 2>&1
netsh advfirewall firewall delete rule name="ZCodeForceLoopbackProxy-user" >nul 2>&1
if exist "C:\Program Files\ZCode\ZCode.exe" netsh advfirewall firewall add rule name="ZCodeForceLoopbackProxy" dir=out program="C:\Program Files\ZCode\ZCode.exe" action=block
if exist "%LOCALAPPDATA%\Programs\ZCode\ZCode.exe" netsh advfirewall firewall add rule name="ZCodeForceLoopbackProxy-user" dir=out program="%LOCALAPPDATA%\Programs\ZCode\ZCode.exe" action=block

echo [4/4] Verification...
netstat -ano | findstr ":8765 :8766" | findstr LISTENING
echo.
echo DONE. Now FULLY QUIT ZCode (tray - Exit) and start it from the Start
echo Menu shortcut. All traffic then flows:
echo   ZCode -^> 8766 (MITM content audit) -^> 8765 (byte accounting) -^> net
echo and any direct connection attempt is firewall-blocked (fail-closed).
pause
