# Navi Build Script
# Frontend (Tauri) + Backend (Python)
# Run: powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$TauriDir = Join-Path $Frontend "src-tauri"
$IconSource = Join-Path $Root "icon.png"

Write-Host ""
Write-Host "=============================================="
Write-Host "       Navi Build Script"
Write-Host "       Frontend (Tauri) + Backend (Python)"
Write-Host "=============================================="
Write-Host ""

# =============================================
# Step 0a: Regenerate Tauri icons from icon.png
# =============================================
if (Test-Path $IconSource) {
    Write-Host "[0/4] Regenerating Tauri icons from $IconSource ..." -ForegroundColor Cyan
    Push-Location $Frontend
    try {
        # fnm 激活（首次运行时 npm/npx 可能还没进 PATH）
        try { fnm env --use-on-cd --shell powershell | Out-String | Invoke-Expression } catch {}
        try { fnm use 2>$null } catch {}

        # 用 Tauri CLI 从单张 icon.png 生成全套图标（32/128/128@2x/ico/icns）
        npx --yes @tauri-apps/cli@latest icon "$IconSource"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] tauri icon generation failed, continuing with existing icons..." -ForegroundColor Yellow
        } else {
            Write-Host "[OK] Tauri icons regenerated in src-tauri/icons/" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
    Write-Host ""
} else {
    Write-Host "[WARN] $IconSource not found, skipping icon regeneration" -ForegroundColor Yellow
}

# =============================================
# Step 0b: Generate Live2D models manifest from frontend/public/live2d
# =============================================
$Live2DDir = Join-Path $Frontend "public\live2d"
$ManifestPath = Join-Path $Live2DDir "models.json"
if (Test-Path $Live2DDir) {
    Write-Host "Generating Live2D models manifest..." -ForegroundColor Cyan
    $manifest = @()
    Get-ChildItem -Path $Live2DDir -Directory | Sort-Object Name | ForEach-Object {
        $model3 = Get-ChildItem -Path $_.FullName -Filter "*.model3.json" -Recurse -File | Select-Object -First 1
        if ($model3) {
            $rel = $model3.FullName.Substring($Live2DDir.Length).TrimStart('\','/').Replace('\','/')
            $manifest += [ordered]@{
                id   = $_.Name.ToLower().Replace(' ', '-')
                name = $_.Name
                src  = "/live2d/$rel"
            }
        }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestPath -Encoding UTF8
    Write-Host "[OK] Wrote $($manifest.Count) models to $ManifestPath" -ForegroundColor Green
    Write-Host ""
}

# =============================================
# Step 1: Build Python backend -> navi-backend.exe
# =============================================
Write-Host "[1/4] Building Python backend (uv + PyInstaller)..." -ForegroundColor Cyan
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

# Tauri 2 sidecar 需要 target-triple 后缀的文件名，且路径要匹配 externalBin 的 scope。
# 我们统一放到 frontend/src-tauri/binaries/ 下，并在 tauri.conf.json 里指向 "binaries/navi-backend"。
$targetTriple = "x86_64-pc-windows-msvc"
$sidecarName = "navi-backend-${targetTriple}.exe"
$binariesDir = Join-Path $TauriDir "binaries"
if (-not (Test-Path $binariesDir)) {
    New-Item -ItemType Directory -Path $binariesDir | Out-Null
}
$sidecarDest = Join-Path $binariesDir $sidecarName

Write-Host "Copying sidecar: backend\dist\navi-backend.exe -> src-tauri\binaries\$sidecarName"
Copy-Item "dist\navi-backend.exe" $sidecarDest -Force

# 同时保留旧位置一份，兼容老的 externalBin 路径 / 手动测试
Copy-Item "dist\navi-backend.exe" (Join-Path "dist" $sidecarName) -Force

Write-Host "[OK] Backend built and placed at: src-tauri\binaries\$sidecarName" -ForegroundColor Green
Write-Host ""

# ─── Backend smoke test: 起 exe -> curl /health -> kill ───
Write-Host "Smoke-testing navi-backend.exe..." -ForegroundColor Cyan
$backendExe = Join-Path $Backend "dist\navi-backend.exe"
$smokeProc = Start-Process -FilePath $backendExe -WindowStyle Hidden -PassThru
$healthOk = $false
try {
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { $healthOk = $true; break }
        } catch {}
        # 若进程已死，提前失败
        if ($smokeProc.HasExited) { break }
    }
} finally {
    if (-not $smokeProc.HasExited) {
        try { Stop-Process -Id $smokeProc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
}

if ($healthOk) {
    Write-Host "[OK] navi-backend.exe /health responded 200" -ForegroundColor Green
} else {
    Write-Host "[ERROR] navi-backend.exe failed smoke test (no /health response within 20s)" -ForegroundColor Red
    $crashLog = Join-Path $env:APPDATA "navi\backend-crash.log"
    if (Test-Path $crashLog) {
        Write-Host "---- Last 40 lines of $crashLog ----" -ForegroundColor Yellow
        Get-Content -Path $crashLog -Tail 40 | Write-Host
        Write-Host "------------------------------------" -ForegroundColor Yellow
    } else {
        Write-Host "Crash log not found at $crashLog (backend may have died before even opening it)." -ForegroundColor Yellow
        Write-Host "Try running manually: $backendExe" -ForegroundColor Yellow
    }
    Read-Host "Press Enter to continue Tauri build anyway, or Ctrl+C to abort"
}
Write-Host ""

# =============================================
# Step 2: Install frontend deps
# =============================================
Write-Host "[2/4] Installing frontend dependencies (fnm + npm)..." -ForegroundColor Cyan
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
Write-Host "[3/4] Building Tauri (with sidecar backend)..." -ForegroundColor Cyan
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