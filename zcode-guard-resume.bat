@echo off
echo Resuming ZCode processes frozen by the snapshot guard...
python "%USERPROFILE%\.zcode-tools\guard_resume.py"
echo.
echo Note: the guard stays armed. If it froze ZCode again, a new snapshot
echo artifact was created - check %USERPROFILE%\.zcode-tools\guard.log
pause
