@echo off
cd /d "%~dp0"

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 goto do_pip_python

py -3 --version >nul 2>&1
if %ERRORLEVEL% equ 0 goto do_pip_py

echo [错误] 未找到 Python。
pause
exit /b 1

:do_pip_python
python -m pip install -r requirements.txt
goto done

:do_pip_py
py -3 -m pip install -r requirements.txt
goto done

:done
if %ERRORLEVEL% neq 0 (
    echo [错误] pip 安装失败。
) else (
    echo [完成] 依赖已安装，可双击 run.bat 启动。
)
pause
exit /b %ERRORLEVEL%
