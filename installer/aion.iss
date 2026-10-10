; Inno Setup script for Aion: installer, updater target and uninstaller.
; Built by scripts/build_installer.py (it passes AppVersion, SourceDir, IconFile, OutputDir).
;
; Per-user install into %LOCALAPPDATA%\Programs\Aion, no admin rights. Settings, models and
; history live in %LOCALAPPDATA%\Aion and survive updates; the uninstaller offers to remove them.
; Self-update runs this installer with /VERYSILENT /RELAUNCH=1 (see src/aion/updater.py).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Aion"
#endif
#ifndef IconFile
  #define IconFile "..\build\aion.ico"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{8C1F4E2A-5B7D-4F3E-9A61-2D0C7B5E9F14}
AppName=Aion
AppVersion={#AppVersion}
AppVerName=Aion {#AppVersion}
AppPublisher=namadeku
AppPublisherURL=https://github.com/namadeku/aion
AppSupportURL=https://github.com/namadeku/aion/issues
AppUpdatesURL=https://github.com/namadeku/aion/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Aion
DefaultGroupName=Aion
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Aion-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Aion.exe
UninstallDisplayName=Aion
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
; Aion is closed by the [Code] below (it holds the AionAppMutex mutex)
CloseApplications=no
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.AionRunning=Aion сейчас запущен. Закрыть его и продолжить?
english.AionRunning=Aion is running. Close it and continue?
russian.DeleteData=Удалить также настройки, скачанные модели, историю и память ассистента?%n%n%1
english.DeleteData=Also delete the settings, downloaded models, history and memory?%n%n%1

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; files of the previous version that the new one no longer has
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Aion"; Filename: "{app}\Aion.exe"; Parameters: "run"; WorkingDir: "{app}"
Name: "{autodesktop}\Aion"; Filename: "{app}\Aion.exe"; Parameters: "run"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Aion.exe"; Parameters: "run"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,Aion}"; Flags: nowait postinstall skipifsilent
; self-update: start the new version after a silent install
Filename: "{app}\Aion.exe"; Parameters: "run"; WorkingDir: "{app}"; Flags: nowait; Check: IsRelaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  AppMutex = 'AionAppMutex';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

function IsRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

procedure KillAion;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Aion.exe', '', SW_HIDE,
    ewWaitUntilTerminated, Code);
  Sleep(1500);
end;

{ Silent runs (self-update) wait up to 30 s for Aion to quit, then close it.
  Interactive runs ask the user. }
function EnsureAionClosed(Silent: Boolean): Boolean;
var
  I: Integer;
begin
  Result := True;
  if Silent then
    for I := 1 to 60 do
    begin
      if not CheckForMutexes(AppMutex) then
        Exit;
      Sleep(500);
    end;
  if not CheckForMutexes(AppMutex) then
    Exit;
  if Silent or (MsgBox(CustomMessage('AionRunning'), mbConfirmation, MB_YESNO) = IDYES) then
    KillAion
  else
    Result := False;
end;

function InitializeSetup: Boolean;
begin
  Result := EnsureAionClosed(WizardSilent);
end;

function InitializeUninstall: Boolean;
begin
  Result := EnsureAionClosed(UninstallSilent);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  { "Start with Windows" from the Aion settings }
  RegDeleteValue(HKCU, RunKey, 'Aion');
  DataDir := ExpandConstant('{localappdata}\Aion');
  if DirExists(DataDir) and not UninstallSilent and
     (MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir]), mbConfirmation,
       MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    DelTree(DataDir, True, True, True);
end;
