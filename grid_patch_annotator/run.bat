@echo off
cd /d "%~dp0"

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 goto use_python

py -3 --version >nul 2>&1
if %ERRORLEVEL% equ 0 goto use_py

echo [错误] 未找到 Python。请安装 Python 3.10+ 并勾选 Add to PATH。
echo 下载: https://www.python.org/downloads/
pause
exit /b 1

:use_python
python -c "import PIL" >nul 2>&1
if %ERRORLEVEL% neq 0 goto install_python
python annotate.py %*
goto finish

:install_python
echo [提示] 正在安装依赖 Pillow ...
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 goto pip_fail
python annotate.py %*
goto finish

:use_py
py -3 -c "import PIL" >nul 2>&1
if %ERRORLEVEL% neq 0 goto install_py
py -3 annotate.py %*
goto finish

:install_py
echo [提示] 正在安装依赖 Pillow ...
py -3 -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 goto pip_fail
py -3 annotate.py %*
goto finish

:pip_fail
echo [错误] 依赖安装失败。请手动运行 install_deps.bat
pause
exit /b 1

:finish
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
    echo.
    echo [错误] 程序退出码 %ERR%，见上方提示。
    pause
)
exit /b %ERR%
