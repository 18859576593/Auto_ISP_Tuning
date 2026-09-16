@echo off
chcp 6508 >nul
title autosp Qt 调参工作台
cd /d %~dp0
python autosp\qt_app.py
pause
