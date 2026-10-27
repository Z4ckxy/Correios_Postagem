@echo off
rem Abre o Controle Correios Itu, antes baixando a versao nova do GitHub se tiver.
rem Quem atualiza e o "Atualizar Controle Correios Itu.bat"; sem ele, so abre o programa.
cd /d "%~dp0"
if exist "Atualizar Controle Correios Itu.bat" ("Atualizar Controle Correios Itu.bat" abrir) else (start "" "Controle Correios Itu.exe")
