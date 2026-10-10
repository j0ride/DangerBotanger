param([string]$PythonExecutable)
$ErrorActionPreference = 'Stop'
$taskProjectDirectory = Split-Path -Parent $PSScriptRoot
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $taskProjectDirectory '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw 'Python do projeto não encontrado. Instale as dependências de build e informe -PythonExecutable.'
}
Push-Location -LiteralPath $taskProjectDirectory
try {
    & $PythonExecutable -m PyInstaller DangerBotanger.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar o executável.' }
    $taskExecutable = Join-Path $taskProjectDirectory 'dist\DangerBotanger.exe'
    $taskGuide = Join-Path $taskProjectDirectory 'dist\LEIA-ME.txt'
    Copy-Item -LiteralPath (Join-Path $taskProjectDirectory 'COMPARTILHAR.txt') -Destination $taskGuide -Force
    Compress-Archive -LiteralPath @($taskExecutable, $taskGuide) -DestinationPath (
        Join-Path $taskProjectDirectory 'dist\DangerBotanger-0.1.2-Windows-x64.zip') -Force
    Write-Output 'Pronto: dist/DangerBotanger-0.1.2-Windows-x64.zip'
} finally {
    Pop-Location
}
