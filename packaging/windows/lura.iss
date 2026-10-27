; Inno Setup script for Lura on Windows.
;   iscc /DAppVersion=2.0.0 packaging\windows\lura.iss
; Expects the PyInstaller folder at dist\lura and writes dist\Lura-Setup-<version>.exe.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E1C1B4A-3B4E-4C7E-9F8A-1A2B3C4D5E6F}
AppName=Lura
AppVersion={#AppVersion}
AppPublisher=Lura
AppPublisherURL=https://github.com/mohamedjs/lura
DefaultDirName={autopf}\Lura
DefaultGroupName=Lura
DisableProgramGroupPage=yes
; Per-user install: no admin prompt, and the app runs as the user anyway.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=Lura-Setup-{#AppVersion}
SetupIconFile=..\icons\lura.ico
UninstallDisplayIcon={app}\LuraApp.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "autostart"; Description: "Start Lura when I sign in"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\lura\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lura"; Filename: "{app}\LuraApp.exe"; Parameters: "run"
Name: "{group}\Lura Dashboard"; Filename: "{app}\lura.exe"; Parameters: "dashboard"
Name: "{group}\Uninstall Lura"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Lura"; Filename: "{app}\LuraApp.exe"; Parameters: "run"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Lura"; \
  ValueData: """{app}\LuraApp.exe"" run"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\LuraApp.exe"; Parameters: "run"; Description: "Start Lura now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM LuraApp.exe /F"; Flags: runhidden; RunOnceId: "StopLura"

[Code]
function NodeInstalled(): Boolean;
var ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C where npx', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not NodeInstalled()) and (not WizardSilent()) then
    MsgBox('Lura''s built-in Chrome, web and files connectors need Node.js (it provides npx).' + #13#10 +
           'Install it from https://nodejs.org when you can — everything else works without it.',
           mbInformation, MB_OK);
end;
