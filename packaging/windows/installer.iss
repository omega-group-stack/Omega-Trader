; Inno Setup script for Omega-Trader.
;
; Produces a single Omega-Trader-Setup-x.y.z.exe: next, next, finish, and a
; Start menu entry that opens the dashboard in the browser.
;
; Compile (on Windows, after PyInstaller):
;   iscc packaging\windows\installer.iss
;
; Expects dist\OmegaTrader\ to exist.

#define AppName       "Omega Trader"
#define AppShortName  "OmegaTrader"
#define AppPublisher  "Omega Group Stack"
#define AppURL        "https://github.com/omega-group-stack/Omega-Trader"
#define AppExe        "OmegaTrader.exe"

; Overridden by CI with /DAppVersion=...
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8E3C9A42-5D77-4B1E-9F0A-6C2D4B8A1E73}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
InfoBeforeFile=before-install.txt
OutputDir=..\..\dist
OutputBaseFilename={#AppShortName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; A per-user install needs no administrator rights and keeps everything the
; app writes inside the user's profile.
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=omega.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\OmegaTrader\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Settings folder"; Filename: "{localappdata}\{#AppShortName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; \
  Description: "Start {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Logs and cache are disposable; config.yaml and the trade journal are the
; user's own records and are deliberately left behind.
Type: filesandordirs; Name: "{localappdata}\{#AppShortName}\logs"
Type: filesandordirs; Name: "{localappdata}\{#AppShortName}\runtime"
