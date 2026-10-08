@echo off
setlocal
set "DANGERBOT_PROJECT_DIR=%~dp0"
powershell.exe -NoLogo -NoProfile -NoExit -Command "$Host.UI.RawUI.WindowTitle = 'DangerBotanger'; Set-Location -LiteralPath $env:DANGERBOT_PROJECT_DIR; $botPython = Join-Path $env:DANGERBOT_PROJECT_DIR '.venv\Scripts\python.exe'; if (-not (Test-Path -LiteralPath $botPython)) { Write-Host 'Ambiente virtual nao encontrado. Consulte a preparacao no README.md.' -ForegroundColor Red; return }; if (-not (Test-Path -LiteralPath '.env')) { Write-Host 'Arquivo .env nao encontrado. Configure suas credenciais antes de iniciar.' -ForegroundColor Red; return }; & $botPython main.py run; Write-Host ''; Write-Host ('Bot encerrado. Codigo de saida: ' + $LASTEXITCODE)"
endlocal
