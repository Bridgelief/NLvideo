# 局域网共享本仓库（git daemon）：让同一个 Wi-Fi 下的伙伴 clone / push
#
# 用法（仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1 -Port 9418
#
# 前置：本机已有 Git；共享裸库默认为 D:\NLvideo.git（可用 -Repo 指定）
# 伙伴命令：git clone git://<本机IP>:9418/NLvideo.git
#
# 注意：git daemon 没有账号体系，同一局域网内谁都能 clone/推送。
#      只适合可信网络（自己家/宿舍/手机热点）；演示完 Ctrl+C 关掉。
#      不要在公共 Wi-Fi/公司网长期开着。

param(
    [int]$Port = 9418,
    [string]$Repo = 'D:\NLvideo.git',
    [string]$BasePath = 'D:\',
    [string]$GitExe = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# 1) 找 git：PATH > 本仓库 _tools 便携版
$git = $GitExe
if (-not $git) {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { $git = $cmd.Source }
}
if (-not $git) {
    $portable = 'D:\videoshoot\_tools\git-full\cmd\git.exe'
    if (Test-Path $portable) { $git = $portable }
}
if (-not $git) {
    Write-Host '[x] 找不到 git：请安装 Git for Windows，或用 -GitExe 指定 git.exe 路径' -ForegroundColor Red
    Write-Host '    下载：https://git-scm.com/download/win' -ForegroundColor Yellow
    exit 1
}

# 2) 检查共享库
if (-not (Test-Path $Repo)) {
    Write-Host "[x] 共享仓库不存在：$Repo" -ForegroundColor Red
    Write-Host '    先创建（在任意克隆里）：git clone --bare . D:\NLvideo.git' -ForegroundColor Yellow
    exit 1
}

# 3) 局域网 IP
$ips = @()
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
        Select-Object -ExpandProperty IPAddress -Unique
} catch { $ips = @() }

# 4) 端口检查
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[x] 端口 $Port 已被占用（PID $($busy.OwningProcess)）：换端口 -Port 9419" -ForegroundColor Red
    exit 1
}

Write-Host '=== NLvideo 局域网 Git 共享 ===' -ForegroundColor Cyan
Write-Host "git       : $git"
Write-Host "共享仓库  : $Repo"
Write-Host "监听端口  : TCP $Port（防火墙需放行，见下）"
Write-Host ''
Write-Host '伙伴执行（同一 Wi-Fi）：' -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host "  git clone git://${ip}:$Port/NLvideo.git" -ForegroundColor Green
}
Write-Host ''
Write-Host '首次需要在 Windows 防火墙放行（管理员 PowerShell 执行一次）：' -ForegroundColor Yellow
Write-Host "  netsh advfirewall firewall add rule name=\"NLvideo git $Port\" dir=in action=allow protocol=TCP localport=$Port" -ForegroundColor Yellow
Write-Host ''
Write-Host '提醒：git daemon 无鉴权，同一局域网可读写；演示完 Ctrl+C 关闭。' -ForegroundColor DarkGray
Write-Host ''

& $git daemon --reuseaddr --informative-errors --export-all --enable=receive-pack `
    "--base-path=$BasePath" "--port=$Port" $Repo
