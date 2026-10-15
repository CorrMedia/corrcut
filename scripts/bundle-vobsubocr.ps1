# Build subtile-ocr (vobsubocr-compatible) and bundle into corrcut resources.
# Prerequisites: Rust/cargo, Visual Studio C++ build tools, vcpkg with tesseract/leptonica,
# and LLVM (libclang) on PATH via LIBCLANG_PATH.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
$Dest = Join-Path $RepoRoot "src\corrcut\resources\vobsubocr\win-amd64"
if (-not $env:VCPKG_ROOT) {
    throw "Set VCPKG_ROOT to your vcpkg clone (with tesseract:x64-windows)."
}
$VcpkgRoot = $env:VCPKG_ROOT
$VcpkgBin = Join-Path $VcpkgRoot "installed\x64-windows\bin"
$TessdataSource = "C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata"

if (-not (Test-Path (Join-Path $VcpkgRoot "vcpkg.exe"))) {
    throw "vcpkg not found at $VcpkgRoot. Clone https://github.com/microsoft/vcpkg and install tesseract:x64-windows."
}

if (-not (Test-Path $TessdataSource)) {
    throw "eng.traineddata not found. Install Tesseract OCR for Windows first."
}

Write-Host "Building subtile-ocr..."
$env:VCPKG_ROOT = $VcpkgRoot
$env:VCPKGRS_TRIPLET = "x64-windows"
$env:VCPKGRS_DYNAMIC = "1"
if (-not $env:LIBCLANG_PATH) {
    $env:LIBCLANG_PATH = "C:\Program Files\LLVM\bin"
}
cargo install subtile-ocr --locked

$SubtileExe = Join-Path $env:USERPROFILE ".cargo\bin\subtile-ocr.exe"
if (-not (Test-Path $SubtileExe)) {
    throw "subtile-ocr build failed; executable not found at $SubtileExe"
}

Write-Host "Bundling into $Dest"
New-Item -ItemType Directory -Force -Path (Join-Path $Dest "tessdata") | Out-Null
Copy-Item $SubtileExe (Join-Path $Dest "vobsubocr.exe") -Force
Copy-Item (Join-Path $VcpkgBin "*.dll") $Dest -Force
Copy-Item $TessdataSource (Join-Path $Dest "tessdata\eng.traineddata") -Force

Write-Host "Done. Bundled vobsubocr.exe + DLLs + tessdata/eng.traineddata"
