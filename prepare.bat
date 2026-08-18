@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=D:\duyiyan\Python311\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo 将启动系统「讲述人」，让微信 4.x 露出界面控件。
echo 讲述人可能会出声，按 CapsLock+M 可静音。
echo.
echo 启动后请：托盘右键微信 → 退出 → 再重新打开登录。
echo.

"%PY%" reply.py --prepare
echo.
pause
