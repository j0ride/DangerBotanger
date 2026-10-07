$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $projectDirectory 'data'
$certificatePath = Join-Path $dataDirectory 'localhost-cert.pem'
$privateKeyPath = Join-Path $dataDirectory 'localhost-key.pem'
if ((Test-Path -LiteralPath $certificatePath) -or (Test-Path -LiteralPath $privateKeyPath)) {
    throw 'Certificado ou chave já existe em data/. Os arquivos existentes não serão substituídos.'
}
$opensslCommand = Get-Command openssl -ErrorAction SilentlyContinue
$opensslPath = if ($opensslCommand) { $opensslCommand.Source } else { Join-Path $env:ProgramFiles 'Git\usr\bin\openssl.exe' }
if (-not (Test-Path -LiteralPath $opensslPath)) {
    throw 'OpenSSL não encontrado. Instale Git for Windows com OpenSSL.'
}
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
& $opensslPath req -x509 -newkey rsa:2048 -sha256 -noenc -days 365 -keyout $privateKeyPath -out $certificatePath -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar certificado local.' }
Write-Output 'Certificado local criado em data/. Nenhum certificado foi instalado no Windows.'
