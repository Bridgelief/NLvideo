# 手机真机 HTTPS 启动入口：手机浏览器只在 https 下允许相机（getUserMedia）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start-https.ps1
#       端口用环境变量 HTTPS_PORT 覆盖（默认 8443）
# 做的事：切仓库根 → 确保开发证书（tools/make_dev_cert.py）→ 探测局域网 IP → 起 uvicorn(TLS)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$env:PYTHONPATH = $Repo
if ($env:HTTPS_PORT) { $Port = [int]$env:HTTPS_PORT } else { $Port = 8443 }

Write-Host '=== AI 拍摄指导 · 手机 HTTPS 启动 ===' -ForegroundColor Cyan
Write-Host "仓库根目录 : $Repo"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host '[x] 找不到 python：请先安装 Python 3.11 并加入 PATH' -ForegroundColor Red
    exit 1
}

# 1) 证书（缺失 / 局域网 IP 变了会自动重新生成；失败直接停）
Write-Host '[1/3] 准备开发证书…'
python tools\make_dev_cert.py
if ($LASTEXITCODE -ne 0) {
    Write-Host '[x] 证书生成失败：看上面的错误，或手动跑 python tools\make_dev_cert.py' -ForegroundColor Red
    exit 1
}
$cert = Join-Path $Repo 'certs\server.pem'
$key = Join-Path $Repo 'certs\server-key.pem'
if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
    Write-Host '[x] 证书文件不存在：certs/server.pem 或 certs/server-key.pem' -ForegroundColor Red
    exit 1
}

# 2) 局域网 IP（手机要和电脑连同一个 Wi-Fi）
Write-Host '[2/3] 探测局域网 IP…'
$ips = @()
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
        Select-Object -ExpandProperty IPAddress -Unique
} catch {
    try {
        $ips = python tools\make_dev_cert.py --detect-only | Select-String '局域网 IP: (.*)' | ForEach-Object { $_.Matches[0].Groups[1].Value } | ForEach-Object { $_ -split ',\s*' }
    } catch { $ips = @() }
}

# 3) 端口检查
Write-Host '[3/3] 检查端口并启动…'
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[x] 端口 $Port 已被占用（PID $($busy.OwningProcess)）。换端口：`$env:HTTPS_PORT=8444; 再运行本脚本" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '手机访问地址（同一 Wi-Fi）：' -ForegroundColor Green
Write-Host "  本机自检 : https://127.0.0.1:$Port" -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host "  手机打开 : https://${ip}:$Port" -ForegroundColor Green
}
Write-Host ''
Write-Host '手机第一次打开会提示「证书不受信任」：' -ForegroundColor Yellow
Write-Host '  Chrome/Edge：点「高级」→「继续前往（不安全）」；Safari：点「显示详细信息」→「访问此网站」' -ForegroundColor Yellow
Write-Host '  然后允许相机 → 浏览器菜单里「添加到主屏幕」就是全屏 App' -ForegroundColor Yellow
Write-Host '（真实相机需要 https；本证书是自签的，只用于开发演示）' -ForegroundColor DarkGray
Write-Host ''

python -m uvicorn server.app:app --host 0.0.0.0 --port $Port --ssl-keyfile $key --ssl-certfile $cert
