@echo off
rem ASCII-only: GBK console + UTF-8 Chinese in bat causes byte-eating token corruption
title autosp Qt Workbench (TXW828)
cd /d "%~dp0"
python autosp\qt_app.py
pause
