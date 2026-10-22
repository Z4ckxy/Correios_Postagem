@echo off
rem Abre o Controle Correios Itu e, antes, baixa a versao nova do GitHub se tiver.
rem Precisa do Git instalado e desta pasta ter sido baixada com "git clone".
rem Sem Git, sem internet ou com o programa ja aberto, so abre a versao atual.
title Controle Correios Itu
cd /d "%~dp0"
set "EXE=Controle Correios Itu.exe"
set "CONFIGS=configuracao.json correios_config.json"

where git >nul 2>nul || goto abrir
git rev-parse --is-inside-work-tree >nul 2>nul || goto abrir

tasklist /fi "imagename eq %EXE%" | "%SystemRoot%\System32\find.exe" /i "%EXE%" >nul && (echo O programa ja esta aberto. A atualizacao fica para a proxima vez. & goto abrir)

echo Procurando atualizacao...
git fetch --quiet origin main || (echo Sem conexao com o GitHub. Abrindo a versao atual. & goto abrir)
for /f %%i in ('git rev-parse HEAD') do set "LOCAL=%%i"
for /f %%i in ('git rev-parse origin/main') do set "REMOTO=%%i"
if "%LOCAL%"=="%REMOTO%" goto abrir

rem nao apaga trabalho de quem mexe no codigo: so atualiza se nada foi alterado/commitado so aqui
git merge-base --is-ancestor HEAD origin/main || (echo Esta pasta tem commits que nao estao no GitHub. Atualizacao pulada. & goto abrir)
git diff --quiet HEAD -- . ":(exclude)configuracao.json" ":(exclude)correios_config.json" ":(exclude)_codigo-fonte/__pycache__" || (echo Esta pasta tem alteracoes no codigo. Atualizacao pulada. & goto abrir)

echo Atualizando...
rem as configuracoes desta maquina (pasta dos dados e acesso aos Correios) sao mantidas
set "BKP=%TEMP%\controle-correios-config"
if exist "%BKP%" rmdir /s /q "%BKP%"
mkdir "%BKP%"
for %%f in (%CONFIGS%) do if exist "%%f" copy /y "%%f" "%BKP%\" >nul
git reset --hard --quiet origin/main || (echo Nao consegui atualizar. Abrindo a versao atual. & goto abrir)
for %%f in (%CONFIGS%) do if exist "%BKP%\%%f" copy /y "%BKP%\%%f" . >nul
echo Atualizado.

:abrir
start "" "%EXE%"
