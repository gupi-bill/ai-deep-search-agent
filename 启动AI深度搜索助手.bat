@echo off
chcp 65001 >nul
cd /d "C:\Users\a\Desktop\vibe coding"
title AI 深度搜索助手
echo.
echo   正在启动程序...（第一次可能要等几秒）
echo.
echo   程序窗口会自己弹出来。
echo   这个黑窗口是它的后台，别关，关了程序就退了。
echo.
"C:\Users\a\.workbuddy\binaries\python\envs\default\Scripts\python.exe" visual_agent.py
pause
