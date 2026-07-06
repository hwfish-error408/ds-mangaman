@echo off
setlocal
cd /d "%~dp0"

py -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [build] PyInstaller not found, installing...
    py -m pip install pyinstaller || goto :error
)

echo [build] Running PyInstaller...
py -m PyInstaller --noconfirm main_gui.spec || goto :error

echo.
echo [build] Done: dist\main_gui.exe
pause
exit /b 0

:error
echo.
echo [build] FAILED
pause
exit /b 1
