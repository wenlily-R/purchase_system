@echo off
chcp 65001 >nul
title 正成能源采购系统 - 一键安装
echo ============================================
echo   正成能源采购系统 V9.0 一键安装
echo ============================================
echo.
cd /d %~dp0

REM ---------- 1. 检查 Python ----------
echo [1/5] 检查 Python 环境...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo  [错误] 未找到 Python! 请先安装 Python 3.11+ (python.org) 并勾选 Add to PATH
    pause
    exit /b 1
)
python --version

REM ---------- 2. 创建虚拟环境 ----------
echo.
echo [2/5] 创建虚拟环境...
if not exist .venv (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo  [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo  .venv 已创建
) else (
    echo  .venv 已存在, 跳过
)

REM ---------- 3. 安装依赖 ----------
echo.
echo [3/5] 安装依赖 (首次约1-2分钟)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %errorlevel% neq 0 (
    echo  [警告] 清华源失败, 尝试默认源...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM ---------- 4. 数据导入 (一键导入) ----------
echo.
echo [4/5] 检查数据...
if exist "data\purchase.db" (
    echo  已存在数据库 data\purchase.db
    if exist "data\purchase.db.init" (
        set /p REPLACE=  是否用包内初始数据覆盖? (Y/N):
        if /i "%REPLACE%"=="Y" (
            copy /y "data\purchase.db.init" "data\purchase.db" >nul
            echo  [OK] 已导入初始数据
        )
    )
) else (
    if exist "data\purchase.db.init" (
        copy /y "data\purchase.db.init" "data\purchase.db" >nul
        echo  [OK] 已自动导入初始数据
    ) else (
        echo  [提示] 无初始数据文件, 系统将自动创建空库
    )
)

REM ---------- 5. 启动系统 ----------
echo.
echo [5/5] 启动系统...
echo.
echo  ============================================
echo    系统启动中, 浏览器访问: http://127.0.0.1:5899
echo    默认账号: admin / admin123
echo    (按 Ctrl+C 停止系统)
echo  ============================================
echo.
".venv\Scripts\python.exe" app.py

pause
