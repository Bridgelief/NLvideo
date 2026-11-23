# 局域网共享本仓库（Git HTTP 智能协议）：让同一个 Wi-Fi 下的伙伴 clone / pull / push
#
# 用法（仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1 -Port 8088
#   powershell -ExecutionPolicy Bypass -File scripts\git-serve.ps1 -Token 123456   # 最简口令
#
# 伙伴命令：git clone http://<本机IP>:8088/NLvideo.git
# 前置：本机有 Python 3.11；共享裸库默认 D:\NLvideo.git
#
# 说明：本机实测 `git daemon`（git://）在 Windows 上推送会卡住，所以改用 HTTP 智能协议
#       （git http-backend），clone / fetch / push 都走同一个地址，端口也更好过防火墙。
# 安全：默认无账号密码，同网设备可读写；只适合可信网络（家/宿舍/手机热点），演示完 Ctrl+C 关闭。

param(
    [int]$Port = 8088,
    [string]$Repo = 'D:\NLvideo.git',
    [string]$Token = '',
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# 1) 找 python
$python = $PythonExe
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Host '[x] 找不到 python：装 Python 3.11+ 并加入 PATH，或用 -PythonExe 指定' -ForegroundColor Red
    exit 1
}

# 2) 检查共享库
if (-not (Test-Path $Repo)) {
    Write-Host "[x] 共享仓库不存在：$Repo" -ForegroundColor Red
    Write-Host '    先创建裸库（在任意克隆里）：git clone --bare . D:\NLvideo.git' -ForegroundColor Yellow
    exit 1
}

# 3) 端口检查
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[x] 端口 $Port 已被占用（PID $($busy.OwningProcess)）：换端口 -Port 8089" -ForegroundColor Red
    exit 1
}

$argsList = @('tools\git_http_server.py', '--repo', $Repo, '--port', "$Port")
if ($Token) { $argsList += @('--token', $Token) }

Write-Host '正在启动共享服务…（Ctrl+C 停止）' -ForegroundColor Cyan
& $python @argsList
