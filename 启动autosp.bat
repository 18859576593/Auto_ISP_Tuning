@echo off
chcp 65001 >nul
title autosp 自动ISP调参工作台
cd /d %~dp0
echo 正在启动 autosp Web 工作台...
python autosp\webapp.py
pause
