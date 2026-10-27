; Instalador do Controle Correios (Inno Setup 6). Gerado pelo gerar_exe.bat, que passa a versao:
;   ISCC /DAppVersion=2026.10.27.1530 instalador.iss
; Instala sem pedir administrador, em %LOCALAPPDATA%\Programs\Controle Correios.
; Rodado com /VERYSILENT (botao "Atualizar agora" do programa) atualiza sem perguntar e abre o programa no fim.

#define AppName "Controle Correios"
#define AppExe "Controle Correios.exe"
#ifndef AppVersion
  #define AppVersion "0"
#endif

[Setup]
AppId={{3D6E1B94-27A5-4C8F-B1E0-5A9C7F2D4E61}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\instalador
OutputBaseFilename=Instalar Controle Correios
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=force

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"

[Files]
Source: "..\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Modelos\*"; DestDir: "{app}\Modelos"; Flags: ignoreversion recursesubdirs
Source: "..\LEIA-ME.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\COMO CONFIGURAR A API DOS CORREIOS.txt"; DestDir: "{app}"; Flags: ignoreversion
; aponta para a pasta dos dados no servidor; se a pessoa trocar a pasta, a atualizacao nao desfaz
Source: "..\configuracao.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent
; atualizacao pelo botao do programa: abre de novo sozinho
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent
