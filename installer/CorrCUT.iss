; Inno Setup script. scripts/build-windows-release.ps1 stages ..\dist\CorrCUT first.

#define AppVersion "0.1.0"
#define SourceDir "..\dist\CorrCUT"

[Setup]
AppId={{A7C3E1B2-4F58-4D1A-9C0E-6B2A8F0D4E11}
AppName=CorrCUT
AppVersion={#AppVersion}
AppPublisher=CorrMedia
DefaultDirName={localappdata}\Programs\CorrCUT
DefaultGroupName=CorrCUT
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=CorrCUT-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\corrcut\resources\corrcut.ico
UninstallDisplayIcon={app}\corrcut.ico
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
InfoBeforeFile=..\src\corrcut\resources\LIMITS.txt
#ifndef Sign
#define Sign "no"
#endif
#if Sign == "yes"
SignTool=corrcut
SignedUninstaller=yes
#endif
UninstallDisplayName=CorrCUT

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "*\__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\src\corrcut\resources\corrcut.ico"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: trustcert; Description: "Trust the CorrMedia certificate for this Windows user"

[Icons]
Name: "{group}\CorrCUT"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m corrcut"; WorkingDir: "{app}"; IconFilename: "{app}\corrcut.ico"
Name: "{autodesktop}\CorrCUT"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m corrcut"; WorkingDir: "{app}"; IconFilename: "{app}\corrcut.ico"

[Run]
Filename: "{sys}\certutil.exe"; Parameters: "-user -addstore Root ""{app}\corrcut-codesign.cer"""; Tasks: trustcert; StatusMsg: "Trusting the CorrMedia certificate..."; Flags: runhidden
Filename: "{sys}\certutil.exe"; Parameters: "-user -addstore TrustedPublisher ""{app}\corrcut-codesign.cer"""; Tasks: trustcert; Flags: runhidden
Filename: "{app}\python\pythonw.exe"; Parameters: "-m corrcut"; Description: "Launch CorrCUT"; Flags: nowait postinstall skipifsilent
