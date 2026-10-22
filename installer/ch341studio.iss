; Instalador Windows do CH341 Studio (Inno Setup 6)
; Gerado pelo build_instalador_windows.bat — não precisa abrir este arquivo à mão.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CH341Studio"
#endif

[Setup]
AppId={{6E0D6B52-3E2C-4D7B-9C1E-34A1C0DE3410}
AppName=CH341 Studio
AppVersion={#AppVersion}
AppVerName=CH341 Studio {#AppVersion}
AppPublisher=GDTech Informática
AppPublisherURL=https://youtube.com/GDTechinformatica
DefaultDirName={autopf}\CH341 Studio
DefaultGroupName=CH341 Studio
DisableProgramGroupPage=yes
; instala só para o usuário atual (não pede administrador); dá para escolher "todos os usuários"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=CH341Studio-{#AppVersion}-instalador-windows
SetupIconFile=..\ch341studio\assets\icon.ico
UninstallDisplayIcon={app}\CH341Studio.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CH341 Studio"; Filename: "{app}\CH341Studio.exe"
Name: "{group}\Desinstalar CH341 Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CH341 Studio"; Filename: "{app}\CH341Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CH341Studio.exe"; Description: "{cm:LaunchProgram,CH341 Studio}"; Flags: nowait postinstall skipifsilent
