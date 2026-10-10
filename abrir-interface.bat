@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Ambiente virtual nao encontrado. Consulte a preparacao no README.md.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "main.py" gui
endlocal
