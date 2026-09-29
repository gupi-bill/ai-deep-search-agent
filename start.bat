@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI 深度搜索助手
echo.
echo   正在启动程序...（第一次可能要等几秒）
echo.
echo   程序窗口会自己弹出来。
echo   这个黑窗口是它的后台，别关，关了程序就退。
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
"%PY%" -c "import webview" >nul 2>nul
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

echo   用的是： %PY%
echo.
%PY% visual_agent.py
pause
