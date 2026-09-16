@echo off
chcp 65001 >nul
title autosp 打包 exe
cd /d %~dp0
echo 正在打包 autosp-webapp.exe（首次较慢，约 1-3 分钟）...
pyinstaller --noconfirm --onefile --name autosp-webapp --distpath . --workpath build --specpath build autosp\webapp.py
if exist autosp-webapp.exe (
  echo.
  echo 打包成功: %cd%\autosp-webapp.exe
  echo 用法: 双击即启动 Web 工作台；exe 需与 platforms/ refs/ data/ 目录同级
) else (
  echo 打包失败，查看上方日志
)
pause
