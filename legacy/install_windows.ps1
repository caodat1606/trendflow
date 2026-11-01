# TrendFlow 3 - cai dat tren Windows 11.
# Chay: chuot phai file nay > "Run with PowerShell", hoac trong PowerShell:
#   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\TrendFlow_3.0\install_windows.ps1"
# An toan: cai vao thu muc moi; KHONG ghi de database da co; du lieu tu Mac chi duoc nhap khi chua co database.
param(
  [string]$Target = "$env:USERPROFILE\TrendFlow_3",
  [string]$DataZip = ""
)
$ErrorActionPreference = 'Stop'
$Src = $PSScriptRoot
function Step($m) { Write-Host ""; Write-Host "▶ $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host "✕ $m" -ForegroundColor Red; Read-Host "Nhấn Enter để đóng"; exit 1 }
function Ok($m) { Write-Host "✓ $m" -ForegroundColor Green }

$CodeFiles = @('app.py','index.html','tf_db.py','tf_jobs.py','tf_llm.py','tf_media.py','tf_news.py','tf_qa.py','tf_render.py',
  'tf_script.py','tf_text.py','tf_tts.py','tf_article.py','tf_verify.py','tf_imagesearch.py','import_data.py','free_port.py','diagnose_tts.py','diagnose_tts.bat','test_tts_pipeline.py','test_tts_pipeline.bat','requirements.txt','requirements-dev.txt',
  'start_windows.bat','install_windows.ps1','README_WINDOWS.md','AUDIT_REPORT.md','TEST_REPORT.md','.env.example')

Step "Kiểm tra gói cài"
foreach ($f in $CodeFiles) { if (-not (Test-Path (Join-Path $Src $f))) { Fail "Thiếu file trong gói cài: $f" } }
if ((Resolve-Path $Src).Path -eq $Target) { Fail "Hãy chạy từ thư mục giải nén, không phải từ $Target" }
Get-ChildItem -Path $Src -Recurse -File | Unblock-File

Step "Kiểm tra Python 3.12"
$py = $null
try { & py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { $py = @('py','-3.12') } } catch {}
if (-not $py) {
  $ans = Read-Host "Chưa có Python 3.12. Cài bằng winget ngay? (y/n)"
  if ($ans -ne 'y') { Fail "Cần Python 3.12: winget install -e --id Python.Python.3.12" }
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  Fail "Đã cài Python. ĐÓNG cửa sổ PowerShell này, mở cửa sổ mới rồi chạy lại trình cài."
}
Ok ((& $py[0] $py[1] --version) -join ' ')

Step "Kiểm tra FFmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  $ans = Read-Host "Chưa có FFmpeg. Cài bằng winget ngay? (y/n)"
  if ($ans -ne 'y') { Fail "Cần FFmpeg: winget install -e --id Gyan.FFmpeg" }
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  Fail "Đã cài FFmpeg. ĐÓNG cửa sổ PowerShell này, mở cửa sổ mới rồi chạy lại trình cài."
}
Ok ((ffmpeg -version | Select-Object -First 1))

Step "Cài chương trình vào $Target"
New-Item -ItemType Directory -Force -Path $Target | Out-Null
if (Test-Path (Join-Path $Target 'app.py')) {
  $bk = Join-Path $env:USERPROFILE ("TrendFlow_Backups\code_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
  New-Item -ItemType Directory -Force -Path $bk | Out-Null
  foreach ($f in $CodeFiles) { $p = Join-Path $Target $f; if (Test-Path $p) { Copy-Item $p $bk } }
  Ok "Đã sao lưu code cũ vào $bk"
}
foreach ($f in $CodeFiles) { Copy-Item (Join-Path $Src $f) (Join-Path $Target $f) -Force }
Copy-Item (Join-Path $Src 'tests') $Target -Recurse -Force

Step "Môi trường Python (.venv)"
$vpy = Join-Path $Target '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { & $py[0] $py[1] -m venv (Join-Path $Target '.venv') }
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r (Join-Path $Target 'requirements.txt')
& $vpy -m pip install -q --upgrade "edge-tts>=7.3.1,<8"
& $vpy -c "import fastapi, edge_tts, PIL, feedparser, bs4, gtts; print('Thư viện Python: OK, edge-tts', edge_tts.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "Cài thư viện Python lỗi." }

Step "Nhập dữ liệu từ Mac"
if (-not $DataZip) {
  $cand = Get-ChildItem -Path "$env:USERPROFILE\Downloads","$env:USERPROFILE\Desktop","$env:USERPROFILE\OneDrive\Desktop" `
          -Filter 'TrendFlow_data_*.zip' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($cand) { $DataZip = $cand.FullName }
}
if ($DataZip) {
  Write-Host "Dùng: $DataZip"
  & $vpy (Join-Path $Target 'import_data.py') $DataZip $Target
  if ($LASTEXITCODE -ne 0) { Fail "Nhập dữ liệu lỗi — dữ liệu trên Mac vẫn an toàn, gửi ảnh màn hình cho Claude." }
} else {
  Write-Host "Không thấy file TrendFlow_data_*.zip trong Downloads/Desktop — sẽ tạo database mới."
}
$envFile = Join-Path $Target '.env'
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $Target '.env.example') $envFile }

Step "Kiểm tra Ollama"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
  $models = (ollama list) -join "`n"
  if ($models -match 'qwen3:8b') { Ok "qwen3:8b đã có" } else { Write-Host "! Cần chạy: ollama pull qwen3:8b" -ForegroundColor Yellow }
} else {
  Write-Host "! Chưa có Ollama: winget install -e --id Ollama.Ollama   rồi: ollama pull qwen3:8b" -ForegroundColor Yellow
}

Step "Tạo lối tắt trên Desktop"
try {
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'TrendFlow.lnk'))
  $lnk.TargetPath = Join-Path $Target 'start_windows.bat'
  $lnk.WorkingDirectory = $Target
  $lnk.Save()
  Ok "Đã tạo lối tắt TrendFlow trên Desktop"
} catch { Write-Host "Không tạo được lối tắt; mở trực tiếp $Target\start_windows.bat" }

Write-Host ""
Ok "Đã cài TrendFlow vào $Target. Nhấp đúp lối tắt 'TrendFlow' trên Desktop để mở."
Read-Host "Nhấn Enter để đóng"
