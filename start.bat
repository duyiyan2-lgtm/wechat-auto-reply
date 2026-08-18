@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=D:\duyiyan\Python311\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo ============================================
echo   微信离线留言（截图识别未读红点）
echo ============================================
echo   请先打开并登录微信，窗口保持可见。
echo   运行时不要锁屏、不要操作鼠标。
echo   按 Ctrl+C 停止。
echo ============================================
echo.

"%PY%" reply.py --check
if errorlevel 1 (
    echo.
    echo 没找到微信窗口。请先打开微信再双击本文件。
    pause
    exit /b 1
)

"%PY%" reply.py
if errorlevel 1 (
    echo.
    echo 自动回复退出异常。
)
echo.
pause
