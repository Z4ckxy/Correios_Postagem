@echo off
rem Gera o "Controle Correios Itu.exe" (na pasta acima desta) e o instalador em ..\instalador.
rem Precisa do Python (python.org) e do Inno Setup 6 (jrsoftware.org). So e usado para atualizar o programa.
rem Para publicar: rode este arquivo, depois de commit e push. Os programas instalados avisam
rem "Versao nova disponivel" na proxima vez que abrirem.
title Gerar Controle Correios Itu.exe
cd /d "%~dp0"

rem versao = data e hora de agora; o programa compara com a publicada no GitHub
for /f %%v in ('powershell -NoProfile -Command "Get-Date -Format yyyy.MM.dd.HHmm"') do set "VERSAO=%%v"
>"%~dp0versao.txt" echo %VERSAO%
echo Versao %VERSAO%

python -m pip install --quiet --upgrade pyinstaller openpyxl
python -m PyInstaller --noconfirm --onefile --windowed --name "Controle Correios Itu" ^
  --add-data "%~dp0interface.html;." ^
  --add-data "%~dp0versao.txt;." ^
  --add-data "%~dp0..\Modelos\Modelo de Tabela TOTVS.xlsx;." ^
  --distpath "%~dp0.." --workpath "%TEMP%\controle-correios-build" --specpath "%TEMP%\controle-correios-build" ^
  "%~dp0servidor.py"
if errorlevel 1 (echo ERRO ao gerar o .exe. Feche o programa se ele estiver aberto e tente de novo. & pause & exit /b 1)

set "ISCC="
for %%p in ("%LOCALAPPDATA%\Programs\Inno Setup 6" "%ProgramFiles(x86)%\Inno Setup 6" "%ProgramFiles%\Inno Setup 6") do if exist "%%~p\ISCC.exe" set "ISCC=%%~p\ISCC.exe"
if not defined ISCC (echo Inno Setup 6 nao encontrado: o .exe foi gerado, o instalador nao. & pause & exit /b 1)
"%ISCC%" /Qp /DAppVersion=%VERSAO% "%~dp0instalador.iss"
if errorlevel 1 (echo ERRO ao gerar o instalador. & pause & exit /b 1)

echo.
echo Pronto: Controle Correios Itu.exe e instalador\Instalar Controle Correios Itu.exe (versao %VERSAO%).
echo Agora de commit e push para os outros computadores receberem.
pause
