@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   中东要闻推送 - 打包 exe
echo ============================================
echo.

rem 优先用带 tkinter + requests 的 Anaconda Python
set PY=E:\ANACONDA\python.exe
if not exist "%PY%" set PY=python

echo [1/3] 生成图标（若缺失）...
"%PY%" make_icon.py
echo.

echo [2/3] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo.

echo [3/3] 开始打包（首次约 1-3 分钟）...
"%PY%" -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [失败] 打包出错，请把上面的红字发给开发者。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成！
echo   产物：dist\中东要闻推送.exe
echo ============================================
dir /b dist
echo.
pause
