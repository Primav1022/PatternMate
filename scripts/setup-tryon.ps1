$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($projectRoot -ne 'F:\CHI27') {
    throw "This installer is restricted to F:\CHI27; resolved root was $projectRoot"
}

$cacheRoot = Join-Path $projectRoot '.cache'
$paths = @(
    (Join-Path $cacheRoot 'pip'),
    (Join-Path $cacheRoot 'tmp'),
    (Join-Path $cacheRoot 'torch'),
    (Join-Path $cacheRoot 'tryon-results')
)
foreach ($path in $paths) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:TEMP = Join-Path $cacheRoot 'tmp'
$env:TMP = Join-Path $cacheRoot 'tmp'
$env:TORCH_HOME = Join-Path $cacheRoot 'torch'
$env:XDG_CACHE_HOME = $cacheRoot
$env:NPM_CONFIG_CACHE = Join-Path $cacheRoot 'npm'

$environment = if ($env:TRYON_ENV) { $env:TRYON_ENV } else { 'F:\Anaconda\envs\pytorch' }
$python = Join-Path $environment 'python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Existing F: drive environment not found: $python"
}

& $python -m pip install --no-cache-dir -r (Join-Path $projectRoot 'apps\tryon-service\requirements.txt')
& $python -m pip install --no-cache-dir smplx trimesh warp-lang
& $python -c "import torch, warp, smplx, fastapi; print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback')"

Write-Host "Try-on environment: $environment"
Write-Host "Cache and generated resources: $cacheRoot"
