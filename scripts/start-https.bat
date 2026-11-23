@echo off
rem =====================================================================
rem 手机真机 HTTPS 启动入口（cmd 版）。手机浏览器只在 https 下允许相机。
rem 用法：双击或  scripts\start-https.bat
rem       换端口：set HTTPS_PORT=8444 再运行
rem =====================================================================
chcp 65001 >nul
setlocal
set "REPO=%~dp0.."
cd /d "%REPO%"
set "PYTHONPATH=%REPO%"
if "%HTTPS_PORT%"=="" set "HTTPS_PORT=8443"

echo === AI 拍摄指导 · 手机 HTTPS 启动 ===
echo 仓库根目录 : %REPO%

python tools\make_dev_cert.py
if errorlevel 1 (
    echo [x] 证书生成失败，请先运行 python tools\make_dev_cert.py 看错误
    exit /b 1
)

echo.
echo 手机访问：同一 Wi-Fi 下打开 https://^<电脑局域网IP^>:%HTTPS_PORT%
echo   第一次会提示证书不受信任：点「高级」-「继续前往」，然后允许相机
echo   浏览器菜单「添加到主屏幕」= 全屏 App
echo.

python -m uvicorn server.app:app --host 0.0.0.0 --port %HTTPS_PORT% --ssl-keyfile "certs\server-key.pem" --ssl-certfile "certs\server.pem"
