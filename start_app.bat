@echo off
cd /d %~dp0
set PYTHONPATH=C:\Python Server\receipt_reader
set PATH=%PATH%;C:\Python Server\receipt_reader
"C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe" -u app.py