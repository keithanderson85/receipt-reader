@echo off
REM Daily backup runner for Windows Task Scheduler.
REM
REM To register this as a scheduled task (runs daily at 2:00 AM):
REM   schtasks /create /tn "ReceiptReaderBackup" /tr "\"%~f0\"" /sc daily /st 02:00 /ru SYSTEM
REM
REM To run manually:
REM   scripts\backup_schedule.bat
REM
REM To check last run result:
REM   schtasks /query /tn "ReceiptReaderBackup" /v /fo list

cd /d "%~dp0.."

REM Use the system Python (venv path may differ per machine)
set PYTHON=C:\Users\keith\AppData\Local\Python\bin\python.exe
if not exist "%PYTHON%" set PYTHON=python

REM Run backup for production environment
"%PYTHON%" scripts\backup.py --env prod

REM Exit code is passed through — Task Scheduler will record failures
exit /b %ERRORLEVEL%
