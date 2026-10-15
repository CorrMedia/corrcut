# Stage a relocatable CorrCUT folder and compile the Windows installer.
# vobsubocr.exe is tracked under resources. FFmpeg, MKVToolNix, and the pinned
# LGPL libmpv build are copied from resources when present and downloaded when
# they are not. Pass -RequireSignature so a missing certificate stops the build.

[CmdletBinding()]
param(
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$PythonVersion = "3.13.7"
# Last BtbN autobuild of September 2026 (kept for two years). Not the floating "latest" tag.
$FfmpegTag = "autobuild-2026-09-30-13-08"
$FfmpegZipName = "ffmpeg-n8.1.3-9-g29e619e767-win64-gpl-8.1.zip"
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegTag/$FfmpegZipName"
$MkvVersion = "86.0"
$MkvUrl = "https://mkvtoolnix.download/windows/releases/$MkvVersion/mkvtoolnix-64-bit-$MkvVersion.7z"
$PaddleVersion = "3.3.0"
$PaddleIndex = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"

$Stage = Join-Path $Root "dist\CorrCUT"
$Cache = Join-Path $Root "dist\cache"
$FfmpegSrc = Join-Path $Root "src\corrcut\resources\ffmpeg\win-amd64"
$MkvSrc = Join-Path $Root "src\corrcut\resources\mkvtoolnix\win-amd64"
$MpvSrc = Join-Path $Root "src\corrcut\resources\libmpv\win-amd64"
$VobSrc = Join-Path $Root "src\corrcut\resources\vobsubocr\win-amd64"

function Save-Url {
    param([string]$Url, [string]$Dest)
    Write-Host "Downloading $Url"
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    $partial = "$Dest.partial"
    if (Test-Path $partial) {
        Remove-Item $partial -Force
    }
    & curl.exe -L --fail --retry 5 --retry-all-errors --retry-delay 2 -o $partial $Url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $partial) -or (Get-Item $partial).Length -lt 1024) {
        throw "Download failed: $Url"
    }
    Move-Item -Path $partial -Destination $Dest -Force
}

function Get-SevenZip {
    foreach ($candidate in @(
            "$env:ProgramFiles\7-Zip\7z.exe",
            "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
        )) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    $zr = Join-Path $Cache "7zr.exe"
    if (-not (Test-Path $zr)) {
        Save-Url "https://www.7-zip.org/a/7zr.exe" $zr
    }
    return $zr
}

function Get-LibmpvPin {
    $loader = Get-Content (Join-Path $Root "src\corrcut\preview\libmpv_loader.py") -Raw
    if ($loader -notmatch '_LIBMPV_WIN_TAG = "([^"]+)"') {
        throw "Could not read _LIBMPV_WIN_TAG from libmpv_loader.py"
    }
    $tag = $Matches[1]
    if ($loader -notmatch '_LIBMPV_WIN_ARCHIVE = "([^"]+)"') {
        throw "Could not read _LIBMPV_WIN_ARCHIVE from libmpv_loader.py"
    }
    $archive = $Matches[1]
    if ($archive -notmatch "lgpl") {
        throw "Refusing to bundle libmpv archive '$archive' because it is not the LGPL build."
    }
    return "https://github.com/zhongfly/mpv-winbuild/releases/download/$tag/$archive"
}

function Copy-NamedFiles {
    param([string]$SourceDir, [string]$DestDir, [string[]]$Names)
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
    foreach ($name in $Names) {
        $path = Join-Path $SourceDir $name
        if (-not (Test-Path $path)) {
            throw "Missing $path"
        }
        Copy-Item $path (Join-Path $DestDir $name) -Force
    }
}

function Find-Iscc {
    foreach ($candidate in @(
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        )) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (-not (Test-Path $kits)) {
        return $null
    }
    $found = Get-ChildItem $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool.exe$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($found) {
        return $found.FullName
    }
    return $null
}

function Resolve-CorrCutSignThumbprint {
    $pfx = $env:CORRCUT_SIGN_PFX
    if ($pfx) {
        if (-not (Test-Path $pfx)) {
            throw "CORRCUT_SIGN_PFX was not found: $pfx"
        }
        if ($null -eq $env:CORRCUT_SIGN_PFX_PASSWORD) {
            throw "Set CORRCUT_SIGN_PFX_PASSWORD for the PFX in CORRCUT_SIGN_PFX."
        }
        $secure = ConvertTo-SecureString -String $env:CORRCUT_SIGN_PFX_PASSWORD -Force -AsPlainText
        $imported = @(Import-PfxCertificate -FilePath $pfx -CertStoreLocation Cert:\CurrentUser\My -Password $secure)
        $withKey = $imported | Where-Object { $_.HasPrivateKey } | Select-Object -First 1
        if (-not $withKey) {
            throw "The PFX did not contain a certificate with a private key."
        }
        return $withKey.Thumbprint.ToUpperInvariant()
    }
    $thumb = $env:CORRCUT_SIGN_THUMBPRINT
    if (-not $thumb) {
        return $null
    }
    $clean = ($thumb -replace '\s', '').ToUpperInvariant()
    if ($clean -notmatch '^[0-9A-F]+$') {
        throw "CORRCUT_SIGN_THUMBPRINT must be the certificate thumbprint in hex."
    }
    $cert = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Thumbprint -eq $clean } |
        Select-Object -First 1
    if (-not $cert) {
        throw "No certificate in Cert:\CurrentUser\My has thumbprint $clean."
    }
    if (-not $cert.HasPrivateKey) {
        throw "Certificate $clean has no private key."
    }
    return $clean
}

function Invoke-CorrCutSign {
    param(
        [Parameter(Mandatory = $true)][string]$Thumbprint,
        [Parameter(Mandatory = $true)][string]$Path
    )
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:SignScript -Thumbprint $Thumbprint -Path $Path
    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed for $Path"
    }
}

$SignThumbprint = Resolve-CorrCutSignThumbprint
if ($RequireSignature -and -not $SignThumbprint) {
    throw @"
This build requires a code-signing certificate.
Set CORRCUT_SIGN_PFX and CORRCUT_SIGN_PFX_PASSWORD, or CORRCUT_SIGN_THUMBPRINT
for a certificate already in Cert:\CurrentUser\My.
The tag workflow reads repository secrets CORRCUT_SIGN_PFX_BASE64 and CORRCUT_SIGN_PFX_PASSWORD.
"@
}
$SignToolPath = $null
if ($SignThumbprint) {
    $SignToolPath = Find-SignTool
    if (-not $SignToolPath) {
        throw "signtool.exe was not found. Install the Windows SDK signing tools and rerun."
    }
    Write-Host "Installer will be signed with certificate $SignThumbprint"
} else {
    Write-Host "Building an unsigned installer. Set CORRCUT_SIGN_PFX or CORRCUT_SIGN_THUMBPRINT to sign it."
}

if (-not (Test-Path (Join-Path $VobSrc "vobsubocr.exe"))) {
    throw "vobsubocr.exe is missing under $VobSrc. Run scripts\bundle-vobsubocr.ps1 first."
}

New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$buildNotes = New-Object System.Collections.Generic.List[string]
$buildNotes.Add("CorrCUT Windows build")
$buildNotes.Add("Python: $PythonVersion embeddable (https://www.python.org/downloads/release/python-3137/)")
$buildNotes.Add("PaddlePaddle: $PaddleVersion CPU ($PaddleIndex)")

$ffmpegBin = Join-Path $Cache "ffmpeg-bin"
if ((Test-Path (Join-Path $FfmpegSrc "ffmpeg.exe")) -and (Test-Path (Join-Path $FfmpegSrc "ffprobe.exe"))) {
    $ffmpegBin = $FfmpegSrc
    $buildNotes.Add("FFmpeg: copied from src/corrcut/resources/ffmpeg/win-amd64")
} else {
    $zip = Join-Path $Cache $FfmpegZipName
    if (-not (Test-Path $zip)) {
        Save-Url $FfmpegUrl $zip
    }
    $extracted = Join-Path $Cache "ffmpeg-extracted"
    if (Test-Path $extracted) {
        Remove-Item $extracted -Recurse -Force
    }
    Expand-Archive -Path $zip -DestinationPath $extracted
    $found = Get-ChildItem $extracted -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $found) {
        throw "ffmpeg.exe was not in $FfmpegUrl"
    }
    $ffmpegBin = $found.DirectoryName
    $buildNotes.Add("FFmpeg: $FfmpegUrl")
}

$mkvBin = Join-Path $Cache "mkvtoolnix-bin"
if ((Test-Path (Join-Path $MkvSrc "mkvmerge.exe")) -and (Test-Path (Join-Path $MkvSrc "mkvextract.exe"))) {
    $mkvBin = $MkvSrc
    $buildNotes.Add("MKVToolNix: copied from src/corrcut/resources/mkvtoolnix/win-amd64")
} else {
    $archive = Join-Path $Cache "mkvtoolnix.7z"
    if (-not (Test-Path $archive)) {
        Save-Url $MkvUrl $archive
    }
    $extracted = Join-Path $Cache "mkvtoolnix-extracted"
    if (Test-Path $extracted) {
        Remove-Item $extracted -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $extracted | Out-Null
    & (Get-SevenZip) "x" $archive "-o$extracted" "-y" | Out-Host
    $found = Get-ChildItem $extracted -Recurse -Filter "mkvmerge.exe" | Select-Object -First 1
    if (-not $found) {
        throw "mkvmerge.exe was not in $MkvUrl"
    }
    $mkvBin = $found.DirectoryName
    $buildNotes.Add("MKVToolNix: $MkvUrl")
}

$mpvBin = $MpvSrc
$mpvDll = Get-ChildItem $MpvSrc -Filter "libmpv*.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
$mpvUrl = Get-LibmpvPin
if ($mpvDll) {
    $buildNotes.Add("libmpv: copied from src/corrcut/resources/libmpv/win-amd64 (LGPL pin $mpvUrl)")
} else {
    $archive = Join-Path $Cache "libmpv.7z"
    if (-not (Test-Path $archive) -or (Get-Item $archive).Length -lt 1MB) {
        if (Test-Path $archive) {
            Remove-Item $archive -Force
        }
        Save-Url $mpvUrl $archive
    }
    $extracted = Join-Path $Cache "libmpv-extracted"
    if (Test-Path $extracted) {
        Remove-Item $extracted -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $extracted | Out-Null
    & (Get-SevenZip) "x" $archive "-o$extracted" "-y" "*.dll" | Out-Host
    $mpvDll = Get-ChildItem $extracted -Recurse -Filter "libmpv*.dll" | Select-Object -First 1
    if (-not $mpvDll) {
        throw "libmpv DLL was not in $mpvUrl"
    }
    $mpvBin = $mpvDll.DirectoryName
    $buildNotes.Add("libmpv: $mpvUrl")
}
$buildNotes.Add("vobsubocr: copied from src/corrcut/resources/vobsubocr/win-amd64")

if (Test-Path $Stage) {
    Remove-Item $Stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$pyZip = Join-Path $Cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pyZip)) {
    Save-Url "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $pyZip
}
$pyDir = Join-Path $Stage "python"
Expand-Archive -Path $pyZip -DestinationPath $pyDir
$pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) {
    throw "Embeddable Python did not include a ._pth file"
}
$pthText = [System.IO.File]::ReadAllText($pth.FullName)
$pthText = $pthText -replace '(?m)^#import site\s*$', 'import site'
[System.IO.File]::WriteAllText($pth.FullName, $pthText)

$python = Join-Path $pyDir "python.exe"
$getPip = Join-Path $Cache "get-pip.py"
if (-not (Test-Path $getPip)) {
    Save-Url "https://bootstrap.pypa.io/get-pip.py" $getPip
}
$env:PYTHONNOUSERSITE = "1"
& $python $getPip
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
# Embeddable Python has no venv module, so pip cannot build an isolated env.
& $python -m pip install hatchling
if ($LASTEXITCODE -ne 0) { throw "hatchling install failed" }
& $python -m pip install --no-build-isolation "${Root}[preview,ai,ocr]"
if ($LASTEXITCODE -ne 0) { throw "package install failed" }
& $python -m pip install "paddlepaddle==$PaddleVersion" -i $PaddleIndex
if ($LASTEXITCODE -ne 0) { throw "PaddlePaddle install failed" }

# Drop unused copyleft / heavy wheels so they are not redistributed.
# language_tool_python is GPL-3 and only used by sup2srt's optional spell-check.
# PySide6 WebEngine / PDF are unused by CorrCUT (no QtWebEngine / QtPdf imports).
Write-Host "Removing unused redistributed packages..."
# WebEngine and PDF wheels share PySide6/__init__.py with Essentials. pip uninstall
# deletes that file, and without it Windows cannot find the Qt DLLs.
$sitePackages = Join-Path $pyDir "Lib\site-packages"
$pysideDir = Join-Path $sitePackages "PySide6"
$pysideKeep = @("__init__.py", "_config.py", "_git_pyside_version.py", "py.typed")
$keptPyside = @{}
foreach ($name in $pysideKeep) {
    $path = Join-Path $pysideDir $name
    if (Test-Path $path) {
        $keptPyside[$name] = [System.IO.File]::ReadAllBytes($path)
    }
}
& $python -m pip uninstall -y language-tool-python PySide6-WebEngine PySide6-Pdf 2>&1 | Out-Host
foreach ($name in $keptPyside.Keys) {
    $path = Join-Path $pysideDir $name
    if (-not (Test-Path $path)) {
        [System.IO.File]::WriteAllBytes($path, $keptPyside[$name])
        Write-Host "Restored PySide6\$name"
    }
}
if (Test-Path $pysideDir) {
    Get-ChildItem $pysideDir -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^(Qt6WebEngine|QtWebEngine|Qt6Pdf|QtPdf|WebEngine|Pdf)'
        } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $pysideDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(QtWebEngine|WebEngine|QtPdf|Pdf)' } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem $sitePackages -Directory -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match '^(language_tool_python|PySide6_WebEngine|PySide6_Pdf|pyside6_webengine|pyside6_pdf)'
    } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$stripCheck = & $python -c @"
import importlib.util, pathlib, sys
site = pathlib.Path(r'$sitePackages')
errors = []
if importlib.util.find_spec('language_tool_python') is not None:
    errors.append('language_tool_python still importable')
init = site / 'PySide6' / '__init__.py'
if not init.is_file():
    errors.append('PySide6/__init__.py missing (Qt DLLs will not load)')
else:
    from PySide6 import QtCore
    if QtCore is None:
        errors.append('PySide6.QtCore import returned None')
for name in ('PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf'):
    if importlib.util.find_spec(name) is not None:
        errors.append(name + ' still importable')
for pattern in ('Qt6WebEngine*.dll', 'Qt6Pdf*.dll'):
    hits = list(site.joinpath('PySide6').glob(pattern)) if (site / 'PySide6').is_dir() else []
    if hits:
        errors.append(pattern + ' still present: ' + hits[0].name)
if errors:
    print('STRIP_FAIL: ' + '; '.join(errors))
    sys.exit(1)
print('STRIP_OK: language_tool_python and Qt WebEngine/Pdf removed')
"@
Write-Host $stripCheck
if ($LASTEXITCODE -ne 0) { throw "Failed to strip unused packages from stage" }

$site = (& $python -c "import corrcut, pathlib; print(pathlib.Path(corrcut.__file__).resolve().parent)").Trim()
$resourceRoot = Join-Path $site "resources"
Copy-NamedFiles $ffmpegBin (Join-Path $resourceRoot "ffmpeg\win-amd64") @("ffmpeg.exe", "ffprobe.exe")
Copy-NamedFiles $mkvBin (Join-Path $resourceRoot "mkvtoolnix\win-amd64") @("mkvmerge.exe", "mkvextract.exe")

$mpvDest = Join-Path $resourceRoot "libmpv\win-amd64"
New-Item -ItemType Directory -Force -Path $mpvDest | Out-Null
Copy-Item (Join-Path $mpvBin "*.dll") $mpvDest -Force

$vobDest = Join-Path $resourceRoot "vobsubocr\win-amd64"
New-Item -ItemType Directory -Force -Path $vobDest | Out-Null
Copy-Item (Join-Path $VobSrc "*") $vobDest -Recurse -Force

Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Stage "LICENSE") -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\THIRD_PARTY_NOTICES.txt") (Join-Path $Stage "THIRD_PARTY_NOTICES.txt") -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\USE.txt") (Join-Path $Stage "USE.txt") -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\LIMITS.txt") (Join-Path $Stage "LIMITS.txt") -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\corrcut-codesign.cer") (Join-Path $Stage "corrcut-codesign.cer") -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\corrcut.ico") (Join-Path $Stage "corrcut.ico") -Force
$licensesSrc = Join-Path $Root "src\corrcut\resources\licenses"
$licensesDest = Join-Path $Stage "licenses"
if (-not (Test-Path $licensesSrc)) {
    throw "Missing license texts at $licensesSrc"
}
New-Item -ItemType Directory -Force -Path $licensesDest | Out-Null
Copy-Item (Join-Path $licensesSrc "*") $licensesDest -Force
# Keep a copy beside the installed package resources (About points here too).
$pkgLicenses = Join-Path $resourceRoot "licenses"
New-Item -ItemType Directory -Force -Path $pkgLicenses | Out-Null
Copy-Item (Join-Path $licensesSrc "*") $pkgLicenses -Force
Copy-Item (Join-Path $Root "src\corrcut\resources\THIRD_PARTY_NOTICES.txt") (
    Join-Path $resourceRoot "THIRD_PARTY_NOTICES.txt"
) -Force
[System.IO.File]::WriteAllLines((Join-Path $Stage "BUILD.txt"), $buildNotes)

@'
@echo off
setlocal
set "ROOT=%~dp0"
if "%~1"=="" (
  start "" "%ROOT%python\pythonw.exe" -m corrcut
) else (
  "%ROOT%python\python.exe" -m corrcut %*
)
'@ | Set-Content -Path (Join-Path $Stage "CorrCUT.cmd") -Encoding ascii

Write-Host "Checking bundled tools..."
$check = & $python -m corrcut --check-tools 2>&1 | Out-String
Write-Host $check
if ($check -match "\[FAIL\]") {
    throw "Bundled ffmpeg/ffprobe/mkvmerge check failed."
}
if (-not (Test-Path (Join-Path $vobDest "vobsubocr.exe"))) {
    throw "Staged install is missing vobsubocr.exe"
}
if (-not (Get-ChildItem $mpvDest -Filter "libmpv*.dll")) {
    throw "Staged install is missing libmpv"
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "Installing Inno Setup 6..."
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    $iscc = Find-Iscc
}
if (-not $iscc) {
    throw "Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isdl.php and rerun."
}

$iss = Join-Path $Root "installer\CorrCUT.iss"
$setup = Join-Path $Root "dist\CorrCUT-Setup.exe"
if ($SignThumbprint) {
    # Inno splits a quoted tool path into extra arguments, so this command has no quotes.
    $env:PATH = "$(Split-Path -Parent $SignToolPath);$env:PATH"
    $SignScript = Join-Path $Root "scripts\sign-authenticode.ps1"
    if ($SignScript -match '\s') {
        throw "The sign script path contains spaces, which Inno Setup cannot pass through: $SignScript"
    }
    # ISCC is 32-bit. Sysnative is the real 64-bit PowerShell from that process.
    $signCmd = 'C:\Windows\Sysnative\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File {0} -Thumbprint {1} -Path $f' -f $SignScript, $SignThumbprint
    & $iscc "/DSign=yes" "/Scorrcut=$signCmd" $iss
} else {
    & $iscc $iss
}
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
if ($SignThumbprint) {
    Invoke-CorrCutSign -Thumbprint $SignThumbprint -Path $setup
}

Write-Host "Installer: $setup"
