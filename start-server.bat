@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI 深度搜索助手 - 本地服务版（浏览器里用）
echo.
echo   正在启动本地服务...
echo   浏览器会自动打开： http://localhost:8080
echo.
echo   用完直接关掉这个黑窗口，服务就停了。
echo   （勾上「看得见浏览器」，你能亲眼看到鼠标自己移动、点击、打字）
echo.

rem ---- 找 Python：优先 venv，其次 py -3，最后 PATH 里的 python ----
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
  echo   [x] 没找到 Python。
  echo.
  echo   请先安装 Python 3.10+ 并勾选 "Add to PATH"： https://www.python.org/downloads/
  echo   或者在项目目录建虚拟环境： python -m venv .venv
  echo.
  pause
  exit /b 1
)

rem ---- 首次运行自动装依赖 ----
"%PY%" -c "import requests" >nul 2>nul
if errorlevel 1 (
  echo   第一次运行，正在安装依赖...（需要几分钟）
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   [x] 依赖安装失败。请手动执行： %PY% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
  )
  echo   依赖装好了。
  echo.
)

start "" /min cmd /c "timeout /t 4 >nul && explorer http://localhost:8080"
%PY% server.py
pause
