; Inno Setup script: wraps dist\Showrunner\ into dist\Showrunner-Setup.exe.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6C1C3E3A-7E52-4E0B-9A63-3F1C2B9D5A11}
AppName=Showrunner
AppVersion={#AppVersion}
AppPublisher=Showrunner
DefaultDirName={autopf}\Showrunner
DefaultGroupName=Showrunner
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Showrunner-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Showrunner.exe

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Showrunner\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Showrunner"; Filename: "{app}\Showrunner.exe"
Name: "{autodesktop}\Showrunner"; Filename: "{app}\Showrunner.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Showrunner.exe"; Description: "{cm:LaunchProgram,Showrunner}"; Flags: nowait postinstall skipifsilent
