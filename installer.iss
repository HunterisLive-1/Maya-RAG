#define MyAppName "BoilerMind"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "BoilerMind"
#define MyAppExeName "BoilerMind.exe"

[Setup]
AppId={{7B2A5C8D-93E1-4F0A-9C2D-A1B2C3D4E5F6}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=BoilerMind_Setup_v{#MyAppVersion}
; Existing install tracked by same AppId is upgraded via [Files] + [InstallDelete] below.
CloseApplications=yes
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableDirPage=no
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=icon.ico
; Optional wizard art (add BMPs next to installer.iss to enable):
; WizardImageFile=assets\installer_banner.bmp
; WizardSmallImageFile=assets\installer_icon.bmp

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[InstallDelete]
; Before copying dist files: remove leftovers from last PyInstaller onedir (books\ and data\ are not listed — user content kept).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\assets"
Type: filesandordirs; Name: "{app}\hud_electron"
Type: files; Name: "{app}\BoilerMind.exe"
Type: files; Name: "{app}\*.dll"
Type: files; Name: "{app}\*.pyd"

[Files]
Source: "dist\BoilerMind\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Always ensure icon.ico is present at app root (used by shortcuts as direct fallback)
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"
Name: "{app}\books"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ApiKeyPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  ApiKeyPage := CreateInputQueryPage(wpSelectTasks,
    'Gemini API key',
    'Optional: paste your Gemini API key',
    'BoilerMind needs a key for Live voice.' + #13#10 +
    'Leave blank and set later in Settings in the HUD.' + #13#10 +
    'Free keys: https://aistudio.google.com');
  ApiKeyPage.Add('Gemini API key:', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvFile: String;
  ApiKey: String;
  Body: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  EnvFile := ExpandConstant('{app}\.env.local');
  ApiKey := ApiKeyPage.Values[0];
  Body :=
    'GEMINI_API_KEY=' + ApiKey + #13#10 +
    'GOOGLE_API_KEY=' + ApiKey + #13#10 +
    'BOILERMIND_TOP_K=5' + #13#10 +
    'BOILERMIND_HUD_PORT=7070' + #13#10 +
    'BOILERMIND_SETTINGS_PORT=7071' + #13#10 +
    'BOILERMIND_VOICE=Laomedeia' + #13#10;
  DeleteFile(EnvFile);
  SaveStringToFile(EnvFile, Body, False);
end;
