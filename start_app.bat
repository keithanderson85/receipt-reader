@echo off
cd /d %~dp0
set PYTHONPATH=%~dp0
set PATH=%PATH%;%~dp0
"C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" -u app.py