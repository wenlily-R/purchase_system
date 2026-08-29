@echo off
chcp 65001 >nul
cd /d %~dp0
echo [1/2] 启动采购系统...
start "采购系统" cmd /c "cd /d %~dp0 && .venv\Scripts\python.exe app.py"
timeout /t 6 /nobreak >nul
echo [2/2] 启动公网隧道(Cloudflare)...
start "公网隧道" cmd /c "cd /d %~dp0\data && cloudflared.exe tunnel --protocol http2 --url http://127.0.0.1:5899 --no-autoupdate"
echo.
echo 本机访问: http://127.0.0.1:5899  (账号 admin / admin123)
echo 公网地址: 启动约10秒后, 打开 data\tunnel.log 查看 "https://....trycloudflare.com" 行
echo 也已在 data\public_url.txt 中更新
pause
