# Navi Build Script
# Frontend (Tauri) + Backend (Python)
# Run: powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

Write-Host ""
Write-Host "=============================================="
Write-Host "       Navi Build Script"
Write-Host "       Frontend (Tauri) + Backend (Python)"
Write-Host "=============================================="
Write-Host ""

# =============================================
# Step 1: Build Python backend -> navi-backend.exe
# =============================================
Write-Host "[1/3] Building Python backend (uv + PyInstaller)..." -ForegroundColor Cyan
Write-Host ""

Set-Location $Backend

Write-Host "Syncing Python deps with uv (including dev)..."
uv sync --dev
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] uv sync failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Clean old builds
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }

Write-Host "Running PyInstaller..."
uv run pyinstaller navi-backend.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Backend build failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path "dist\navi-backend.exe")) {
    Write-Host "[ERROR] dist\navi-backend.exe not found!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""

# Tauri 2 sidecar 需要 target-triple 后缀的文件名
# 当前平台是 x86_64-pc-windows-msvc
$targetTriple = "x86_64-pc-windows-msvc"
$sidecarName = "navi-backend-${targetTriple}.exe"
$sidecarPath = Join-Path "dist" $sidecarName

Write-Host "Renaming for Tauri sidecar: navi-backend.exe -> $sidecarName"
Copy-Item "dist\navi-backend.exe" $sidecarPath

Write-Host "[OK] Backend built: backend\$sidecarPath" -ForegroundColor Green
Write-Host ""

# =============================================
# Step 2: Install frontend deps
# =============================================
Write-Host "[2/3] Installing frontend dependencies (fnm + npm)..." -ForegroundColor Cyan
Write-Host ""

Set-Location $Frontend

# Activate fnm and switch to project Node version
Write-Host "Switching to Node version from .node-version..."
fnm env --use-on-cd --shell powershell | Out-String | Invoke-Expression
fnm use
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] fnm use failed, trying with current Node..." -ForegroundColor Yellow
}

# Verify npm is available
$npmPath = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmPath) {
    Write-Host "[ERROR] npm not found! Make sure fnm is installed and working." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing npm packages..."
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] npm install failed!" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

Write-Host "[OK] Frontend deps ready" -ForegroundColor Green
Write-Host ""

# =============================================
# Step 3: Tauri build (with sidecar)
# =============================================
Write-Host "[3/3] Building Tauri (with sidecar backend)..." -ForegroundColor Cyan
Write-Host ""

npm run tauri build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Tauri build failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "=============================================="
Write-Host "  Build Complete!"
Write-Host ""
Write-Host "  Output: frontend\src-tauri\target\release\bundle\"
Write-Host "=============================================="
Write-Host ""

# Open output folder
$bundlePath = Join-Path $Frontend "src-tauri\target\release\bundle"
if (Test-Path $bundlePath) {
    Start-Process $bundlePath
}

Read-Host "Press Enter to exit"
