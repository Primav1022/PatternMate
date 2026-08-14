$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cacheRoot = Join-Path $projectRoot '.cache'
foreach ($folder in @('pip', 'tmp', 'torch', 'tryon-results')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $cacheRoot $folder) | Out-Null
}

$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:TEMP = Join-Path $cacheRoot 'tmp'
$env:TMP = Join-Path $cacheRoot 'tmp'
$env:TORCH_HOME = Join-Path $cacheRoot 'torch'
$env:XDG_CACHE_HOME = $cacheRoot
$env:NPM_CONFIG_CACHE = Join-Path $cacheRoot 'npm'
$env:TRYON_RESULT_DIR = Join-Path $cacheRoot 'tryon-results'
$env:ENABLE_RESEARCH_3D = if ($env:ENABLE_RESEARCH_3D) { $env:ENABLE_RESEARCH_3D } else { 'true' }
$env:TRYON_DEVICE = if ($env:TRYON_DEVICE) { $env:TRYON_DEVICE } else { 'cuda:0' }
$env:SMPL_MODEL_DIR = if ($env:SMPL_MODEL_DIR) { $env:SMPL_MODEL_DIR } else { Join-Path $projectRoot 'tmp\SMPL_python_v.1.1.0\smpl\models' }

$candidates = @(
    $env:TRYON_PYTHON,
    'F:\Anaconda\envs\pytorch\python.exe',
    (Join-Path $projectRoot '.venv-tryon\Scripts\python.exe')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$python = $candidates | Select-Object -First 1
if (-not $python) {
    throw 'No 3D Python runtime found. Set TRYON_PYTHON to an existing F: drive environment.'
}

Write-Host "3D Python: $python"
Write-Host "Cache and generated resources: $cacheRoot"
& $python -m uvicorn app:app --app-dir (Join-Path $projectRoot 'apps\tryon-service') --host 127.0.0.1 --port 8790
