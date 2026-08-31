@echo off
chcp 65001 >nul
cd /d "C:\Users\a\Desktop\vibe coding"
title AI 深度搜索助手 - 本地服务版（浏览器里用）
echo.
echo   正在启动本地服务...
echo   浏览器会自动打开： http://localhost:8080
echo.
echo   用完直接关掉这个黑窗口，服务就停了。
echo   （勾上「看得见浏览器」，你能亲眼看到鼠标自己移动、点击、打字）
echo.
start "" /min cmd /c "timeout /t 4 >nul && explorer http://localhost:8080"
"C:\Users\a\.workbuddy\binaries\python\envs\default\Scripts\python.exe" server.py
pause
