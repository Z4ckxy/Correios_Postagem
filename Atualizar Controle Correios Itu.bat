<# : parte do cmd; o resto do arquivo e PowerShell (ja vem no Windows, nao precisa instalar nada)
@echo off
title Atualizar Controle Correios Itu
set "CC_BAT=%~f0"
set "CC_PASTA=%~dp0"
set "CC_MODO=%~1"
rem tudo numa linha: a atualizacao pode trocar este proprio arquivo enquanto ele roda
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:CC_BAT))" & exit /b
#>

# Atualiza o Controle Correios Itu para a ultima versao do GitHub.
# Pode ficar em qualquer pasta: copia antiga, pasta vazia ou atualizada.
# Mantem configuracao.json, correios_config.json e a pasta Saida desta maquina.
# Com "abrir" (usado pelo "Abrir Controle Correios Itu.bat"): nao pergunta nada e abre o programa no fim.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # a barra de progresso deixa o download muito lento
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repo = "Z4ckxy/Correios"
$pasta = $env:CC_PASTA.TrimEnd("\")
$abrir = $env:CC_MODO -eq "abrir"
$exe = Join-Path $pasta "Controle Correios Itu.exe"
$marca = Join-Path $pasta "versao.txt"
$api = if ($env:CORREIOS_API) { $env:CORREIOS_API } else { "https://api.github.com/repos/$repo/commits/main" }
$zipUrl = if ($env:CORREIOS_ZIP) { $env:CORREIOS_ZIP } else { "https://github.com/$repo/archive/{0}.zip" }
$manter = "configuracao.json", "correios_config.json"

function Fim {
    if ($abrir) {
        if (Test-Path $exe) { Start-Process -FilePath $exe -WorkingDirectory $pasta }
    } else {
        Write-Host ""
        Read-Host "Aperte Enter para fechar" | Out-Null
    }
    exit
}

try {
    if (Get-Process -Name "Controle Correios Itu" -ErrorAction SilentlyContinue) {
        Write-Host "O programa esta aberto. Feche o Controle Correios Itu para atualizar."
        Fim
    }
    # pasta de quem mexe no codigo (git): nao sobrescreve sem perguntar
    if (Test-Path (Join-Path $pasta ".git")) {
        if ($abrir) { Fim }
        Write-Host "ATENCAO: esta pasta e a do codigo-fonte (git). Atualizar sobrescreve o que foi mudado aqui."
        if ((Read-Host "Atualizar mesmo assim? [S/N]") -notmatch "^[sS]") { Fim }
    }

    Write-Host "Procurando a ultima versao..."
    $sha = "$(Invoke-RestMethod -Uri $api -UseBasicParsing -Headers @{ Accept = "application/vnd.github.sha"; "User-Agent" = "ControleCorreiosItu" })".Trim()
    if ($sha -notmatch "^[0-9a-f]{40}$") { throw "Resposta inesperada do GitHub." }
    $atual = if (Test-Path $marca) { (Get-Content $marca -Raw).Trim() } else { "" }
    if ($sha -eq $atual -and (Test-Path $exe)) {
        Write-Host "Ja esta na ultima versao."
        Fim
    }

    Write-Host "Baixando a versao nova..."
    $tmp = Join-Path $env:TEMP "controle-correios-atualizar"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp "versao.zip"
    Invoke-WebRequest -Uri ($zipUrl -f $sha) -OutFile $zip -UseBasicParsing -Headers @{ "User-Agent" = "ControleCorreiosItu" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $tmp "x"))
    $origem = (Get-ChildItem (Join-Path $tmp "x") -Directory | Select-Object -First 1).FullName

    Write-Host "Atualizando..."
    # configuracoes que esta maquina ja tem ficam como estao; pasta nova recebe as do GitHub
    $pular = @($manter | Where-Object { Test-Path (Join-Path $pasta $_) })
    $args_ = @($origem, $pasta, "/E", "/R:2", "/W:2", "/NJH", "/NJS", "/NFL", "/NDL", "/NP", "/XD", ".git", "Saida")
    if ($pular) { $args_ += @("/XF") + $pular }
    & robocopy @args_ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Nao consegui copiar os arquivos (algum pode estar aberto)." }
    Set-Content -Path $marca -Value $sha -Encoding ASCII
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Atualizado para a versao $($sha.Substring(0, 7))."
} catch {
    Write-Host "Nao consegui atualizar: $($_.Exception.Message)"
    Write-Host "Confira a internet. O programa continua na versao atual."
}
Fim
