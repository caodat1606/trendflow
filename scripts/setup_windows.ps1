# TrendFlow — cài đặt một lần trên Windows (từ thư mục đã git clone).
# Chạy:  powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\TrendFlow\scripts\setup_windows.ps1"
# Làm gì: kiểm tra Git/Python/FFmpeg · tạo .venv · tạo thư mục dữ liệu riêng ngoài mã nguồn
#         · CHÉP (không di chuyển) dữ liệu từ bản cài ZIP cũ · thêm lệnh 'trendflow' vào PATH · lối tắt Desktop (tuỳ chọn).
# Không xoá, không ghi đè database đã có.
param(
  [string]$DataDir = "$env:USERPROFILE\TrendFlowData",
  [string]$OldInstall = "$env:USERPROFILE\TrendFlow_3",
  [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
function Step($m) { Write-Host ""; Write-Host "▶ $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "✓ $m" -ForegroundColor Green }
function Fail($m) { Write-Host ""; Write-Host "✕ $m" -ForegroundColor Red; exit 1 }

Step "Kiểm tra công cụ"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Fail "Chưa có Git. Chạy: winget install -e --id Git.Git  — rồi mở PowerShell MỚI và chạy lại lệnh cài."
}
Ok (git --version)
$py = $null
try { & py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @('py', '-3.12') } } catch {}
if (-not $py) { Fail "Cần Python 3.12: winget install -e --id Python.Python.3.12  — rồi mở PowerShell MỚI và chạy lại." }
Ok ((& $py[0] $py[1] --version) -join ' ')
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Fail "Chưa có FFmpeg: winget install -e --id Gyan.FFmpeg  — rồi mở PowerShell MỚI và chạy lại."
}
Ok "FFmpeg"

Step "Môi trường Python (.venv trong $Root)"
$vpy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { & $py[0] $py[1] -m venv (Join-Path $Root '.venv') }
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail "Cài thư viện Python lỗi." }
Ok "Thư viện Python"

Step "Thư mục dữ liệu riêng: $DataDir"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
[Environment]::SetEnvironmentVariable('TRENDFLOW_DATA_DIR', $DataDir, 'User')
$env:TRENDFLOW_DATA_DIR = $DataDir
if (Test-Path (Join-Path $OldInstall 'trendflow.db')) {
  Write-Host "Chép dữ liệu từ bản cũ $OldInstall (bản cũ giữ nguyên)…"
  & $vpy (Join-Path $Root 'scripts\migrate_data.py') $OldInstall $DataDir
  if ($LASTEXITCODE -ne 0) { Fail "Chép dữ liệu lỗi — dữ liệu cũ vẫn an toàn ở $OldInstall." }
}
if (-not (Test-Path (Join-Path $DataDir '.env'))) { Copy-Item (Join-Path $Root '.env.example') (Join-Path $DataDir '.env') }
Ok "Dữ liệu"

Step "Thêm lệnh 'trendflow' vào PATH"
$bin = Join-Path $Root 'bin'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $userPath) { $userPath = '' }
if (($userPath -split ';') -notcontains $bin) {
  [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';'), 'User')
  Ok "Đã thêm $bin vào PATH (có hiệu lực ở cửa sổ PowerShell MỚI)"
} else { Ok "PATH đã có $bin" }

if (-not $NoShortcut) {
  try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'TrendFlow.lnk'))
    $lnk.TargetPath = Join-Path $bin 'trendflow.cmd'
    $lnk.WorkingDirectory = $Root
    $lnk.Save()
    Ok "Lối tắt TrendFlow trên Desktop (tuỳ chọn) trỏ tới lệnh mới"
  } catch { Write-Host "Không tạo được lối tắt — không sao, dùng lệnh trendflow." }
}

Write-Host ""
Ok "Xong. Mở PowerShell MỚI và gõ:  trendflow"
Write-Host "   trendflow status · trendflow stop · trendflow update · trendflow backup · trendflow logs"
