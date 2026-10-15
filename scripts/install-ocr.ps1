# Create/update the project venv (Python 3.12 or 3.13) and install PaddleOCR.
# PaddlePaddle does not support Python 3.14+.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pyLauncher = $null
foreach ($version in @("3.13", "3.12")) {
    try {
        $probe = & py "-$version" -c "import sys; print(sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pyLauncher = "-$version"
            break
        }
    } catch {
        continue
    }
}

if (-not $pyLauncher) {
    Write-Error "Python 3.12 or 3.13 is required. Install from https://www.python.org/downloads/"
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating .venv with py $pyLauncher ..."
    & py $pyLauncher -m venv .venv
}

Write-Host "Installing OCR dependencies ..."
& $venvPython -m pip install -U pip
& $venvPython -m pip install -e ".[ocr]"

# paddlepaddle and paddlepaddle-gpu conflict if both are installed.
# Pascal, Volta, and Turing (compute capability below 8.0) need the CUDA 11.8
# wheel. CUDA 12.6 does not ship kernels for a GTX 1070 and OCR comes out wrong.
& $venvPython -m pip uninstall -y paddlepaddle paddlepaddle-gpu
$cudaIndex = "cu126"
$capLine = ""
try {
    $capLine = (& nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
} catch {
    $capLine = ""
}
if ($capLine -match '^\s*(\d+(\.\d+)?)') {
    $cap = [double]$Matches[1]
    if ($cap -lt 8.0) {
        $cudaIndex = "cu118"
        Write-Host "GPU compute capability $cap uses the CUDA 11.8 Paddle build."
    } else {
        Write-Host "GPU compute capability $cap uses the CUDA 12.6 Paddle build."
    }
}
$gpuOk = $false
& $venvPython -m pip install "paddlepaddle-gpu==3.3.0" -i "https://www.paddlepaddle.org.cn/packages/stable/$cudaIndex/"
if ($LASTEXITCODE -eq 0) {
    $countRaw = & $venvPython -c "import paddle; n = paddle.device.cuda.device_count() if paddle.device.is_compiled_with_cuda() else 0; print(int(n))"
    if ($LASTEXITCODE -eq 0 -and "$countRaw".Trim() -match '^[1-9]') {
        $gpuOk = $true
        Write-Host "PaddlePaddle GPU ($cudaIndex) is active ($($countRaw.Trim()) device(s))."
    }
}
if (-not $gpuOk) {
    Write-Host "GPU wheel unavailable or no CUDA device. OCR will stay on CPU."
    Write-Host "GPU needs an NVIDIA driver. The full CUDA toolkit is not required."
    & $venvPython -m pip uninstall -y paddlepaddle paddlepaddle-gpu
    & $venvPython -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
    if ($LASTEXITCODE -ne 0) {
        Write-Error "CPU PaddlePaddle install failed"
    }
}
# Paddle and PyTorch both ship libiomp5md.dll. Import torch first or
# paddleocr's modelscope import dies with WinError 127 on torch\lib\shm.dll.
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
& $venvPython -c "import torch; import paddle; from paddleocr import PaddleOCR; print('PaddleOCR OK on', paddle.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "PaddleOCR import failed. See the traceback above."
}

Write-Host ""
Write-Host "IMPORTANT: launch the app with the venv interpreter, not system python:"
Write-Host "  .\.venv\Scripts\python.exe -m corrcut"
Write-Host ""
Write-Host "If you run `python -m corrcut` and `python` is 3.14, PaddleOCR will"
Write-Host "not load and bitmap OCR will refuse to run."
Write-Host ""
Write-Host "Activate with: .\.venv\Scripts\Activate.ps1"
