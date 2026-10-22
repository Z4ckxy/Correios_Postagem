@echo off
rem Gera o "Controle Correios Itu.exe" na pasta acima desta, a partir do codigo-fonte.
rem Precisa do Python instalado (python.org). So e usado para atualizar o programa.
title Gerar Controle Correios Itu.exe
cd /d "%~dp0"
python -m pip install --quiet --upgrade pyinstaller openpyxl
python -m PyInstaller --noconfirm --onefile --windowed --name "Controle Correios Itu" ^
  --add-data "%~dp0interface.html;." ^
  --add-data "%~dp0..\Modelos\Modelo de Tabela TOTVS.xlsx;." ^
  --distpath "%~dp0.." --workpath "%TEMP%\controle-correios-build" --specpath "%TEMP%\controle-correios-build" ^
  "%~dp0servidor.py"
echo.
if errorlevel 1 (echo ERRO ao gerar o .exe. Feche o programa se ele estiver aberto e tente de novo.) else (echo Pronto: Controle Correios Itu.exe atualizado.)
pause
