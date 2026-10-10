; Inno Setup script for Aion: installer, updater target and uninstaller.
; Built by scripts/build_installer.py (it passes AppVersion, SourceDir, IconFile, OutputDir and
; generates the model lists build/models_*.iss from aion.models).
;
; Per-user install into %LOCALAPPDATA%\Programs\Aion, no admin rights. Settings, models and
; history live in %LOCALAPPDATA%\Aion and survive updates; the uninstaller offers to remove them.
; Self-update runs this installer with /VERYSILENT /RELAUNCH=1 (see src/aion/updater.py).
;
; A fresh machine gets everything here, with visible progress: the Edge WebView2 runtime (the
; window needs it, older Windows 10 may lack it), the default voice models and, optionally,
; Ollama. Silent runs (updates) download nothing; the app fetches whatever is still missing.

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
#ifndef ModelIncludes
  #define ModelIncludes "..\build"
#endif
#define ModelsDir "{localappdata}\Aion\models"

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
russian.Extras=Дополнительно:
english.Extras=Extras:
russian.OllamaTask=Установить Ollama для свободного разговора (≈1,6 ГБ; модель qwen3:8b ≈5 ГБ Aion скачает при первом запуске)
english.OllamaTask=Install Ollama for free conversation (~1.6 GB; Aion downloads the qwen3:8b model, ~5 GB, on first start)
russian.MB=МБ
english.MB=MB
russian.Downloading=Скачивание компонентов
english.Downloading=Downloading components
russian.DownloadingDesc=Голосовые модели и нужные программы скачиваются один раз.
english.DownloadingDesc=Voice models and required programs are downloaded once.
russian.DownloadFailed=Не удалось скачать %1:%n%2%n%nУстановка продолжится, недостающие модели Aion скачает при первом запуске.
english.DownloadFailed=Could not download %1:%n%2%n%nSetup continues; Aion downloads the missing models on first start.
russian.InstallingWebView2=Устанавливаю Microsoft Edge WebView2…
english.InstallingWebView2=Installing Microsoft Edge WebView2…
russian.InstallingOllama=Устанавливаю Ollama, это может занять пару минут…
english.InstallingOllama=Installing Ollama, this may take a couple of minutes…
russian.WebView2Failed=Не удалось установить WebView2 (код %1). Aion откроется в браузере.
english.WebView2Failed=Could not install WebView2 (code %1). Aion will open in the browser.
russian.OllamaFailed=Не удалось установить Ollama (код %1). Её можно поставить позже: https://ollama.com
english.OllamaFailed=Could not install Ollama (code %1). You can install it later: https://ollama.com

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "ollama"; Description: "{cm:OllamaTask}"; GroupDescription: "{cm:Extras}"; Check: not OllamaInstalled

[InstallDelete]
; files of the previous version that the new one no longer has
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; voice models downloaded by the wizard; they belong to the data dir and survive the uninstaller
#include ModelIncludes + "\models_files.iss"

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
  WebView2Key = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2Setup = 'MicrosoftEdgeWebview2Setup.exe';
  OllamaUrl = 'https://ollama.com/download/OllamaSetup.exe';
  OllamaSetup = 'OllamaSetup.exe';

var
  DownloadPage: TDownloadWizardPage;

#include ModelIncludes + "\models_code.iss"

function IsRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function HasWebView2(RootKey: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, WebView2Key, 'pv', Version) and (Version <> '') and
    (Version <> '0.0.0.0');
end;

{ The same check as pywebview: without the runtime it falls back to Internet Explorer }
function WebView2Installed: Boolean;
begin
  Result := HasWebView2(HKCU) or HasWebView2(HKLM32) or HasWebView2(HKLM64);
end;

function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe')) or
    FileExists(ExpandConstant('{commonpf64}\Ollama\ollama.exe'));
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

// The file name and megabytes instead of a bare URL ("model.bin: 120 / 484 MB")
function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
var
  Title: String;
  I: Integer;
begin
  Title := FileName;
  if Pos('aion-model-', FileName) = 1 then
  begin
    I := Length(Url);
    while (I > 0) and (Url[I] <> '/') do
      I := I - 1;
    Title := Copy(Url, I + 1, MaxInt);
  end;
  if ProgressMax > 0 then
    Title := Format('%s: %d / %d %s', [Title, Integer(Progress div 1000000),
      Integer(ProgressMax div 1000000), CustomMessage('MB')])
  else
    Title := Format('%s: %d %s', [Title, Integer(Progress div 1000000), CustomMessage('MB')]);
  DownloadPage.Msg2Label.Caption := Title;
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(CustomMessage('Downloading'),
    CustomMessage('DownloadingDesc'), @OnDownloadProgress);
end;

// Download what was added to the page into the temp dir. A failure is reported and setup goes
// on: the app downloads missing models itself, WebView2 and Ollama are not needed to start it.
procedure DownloadQueued;
begin
  try
    DownloadPage.Download;
  except
    if not DownloadPage.AbortedByUser then
      SuppressibleMsgBox(FmtMessage(CustomMessage('DownloadFailed'), [
        DownloadPage.LastBaseNameOrUrl, GetExceptionMessage]), mbError, MB_OK, IDOK);
  end;
  DownloadPage.Clear;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID <> wpReady) or WizardSilent then
    Exit;
  DownloadPage.Clear;
  DownloadPage.Show;
  try
    { programs and models in separate batches: one failure does not cancel the rest }
    if not WebView2Installed then
      DownloadPage.Add(WebView2Url, WebView2Setup, '');
    if WizardIsTaskSelected('ollama') then
      DownloadPage.Add(OllamaUrl, OllamaSetup, '');
    DownloadQueued;
    AddModelDownloads(DownloadPage, ExpandConstant('{#ModelsDir}'));
    DownloadQueued;
  finally
    DownloadPage.Hide;
  end;
end;

procedure RunDownloadedSetup(const FileName, Params, Status, Failure: String);
var
  Path: String;
  Code: Integer;
begin
  Path := ExpandConstant('{tmp}\' + FileName);
  if not FileExists(Path) then
    Exit;
  WizardForm.StatusLabel.Caption := Status;
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if not Exec(Path, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      SuppressibleMsgBox(FmtMessage(Failure, [IntToStr(Code)]), mbError, MB_OK, IDOK);
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  RunDownloadedSetup(WebView2Setup, '/silent /install', CustomMessage('InstallingWebView2'),
    CustomMessage('WebView2Failed'));
  RunDownloadedSetup(OllamaSetup, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES',
    CustomMessage('InstallingOllama'), CustomMessage('OllamaFailed'));
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
