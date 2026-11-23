# 唯一推荐启动入口：powershell -ExecutionPolicy Bypass -File D:\videoshoot\scripts\start.ps1
# 做的事：切到仓库根、设置 PYTHONPATH、检查 python/data/engine/端口，然后起 uvicorn。
# 端口用环境变量 PORT 覆盖（默认 8000）：
#   $env:PORT=8011; powershell -ExecutionPolicy Bypass -File scripts\start.ps1

$ErrorActionPreference = 'Stop'

# 控制台/重定向输出统一用 UTF-8，避免中文提示乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$env:PYTHONPATH = $Repo
if ($env:PORT) { $Port = [int]$env:PORT } else { $Port = 8000 }

Write-Host '=== AI 现场拍摄指导助手 · 启动检查 ===' -ForegroundColor Cyan
Write-Host "仓库根目录 : $Repo"
Write-Host "Python     : $(Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)"

# 1) python 可用性
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host '[x] 找不到 python：请先安装 Python 3.11 并加入 PATH' -ForegroundColor Red
    exit 1
}
$pyVersion = (& python --version 2>&1) -join ' '
Write-Host "[v] $pyVersion"

# 2) data/ 关键文件
$dataFiles = @('data\intent_weights.json', 'data\rules.json', 'data\phrases.json', 'data\edit_rules.json', 'data\materials.csv')
$missingData = @()
foreach ($rel in $dataFiles) {
    if (-not (Test-Path (Join-Path $Repo $rel))) { $missingData += $rel }
}
if ($missingData.Count -gt 0) {
    Write-Host ('[!] 缺少数据文件（服务仍会启动，但建议/粗剪会降级并在 warnings 说明）: ' + ($missingData -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host '[v] data/ 关键文件齐全'
}

# 3) engine/ 就绪情况（惰性 import，不会阻塞启动）
$engineFiles = @('engine\suggest_engine.py', 'engine\edit_engine.py', 'engine\edl_export.py')
$missingEngine = @()
foreach ($rel in $engineFiles) {
    if (-not (Test-Path (Join-Path $Repo $rel))) { $missingEngine += $rel }
}
if ($missingEngine.Count -gt 0) {
    Write-Host ('[!] engine 尚未就绪（服务会用服务端兜底逻辑，warnings 里会说明）: ' + ($missingEngine -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host '[v] engine/ 就绪'
}

# 4) ffmpeg 提示（本机预期没有，不影响启动）
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host '[v] 检测到 ffmpeg，可真实渲染粗剪'
} else {
    Write-Host '[i] 未检测到 ffmpeg：粗剪只导出 EDL/命令/.bat（本机预期行为）'
}

# 5) 端口占用检查
function Test-PortInUse([int]$PortToCheck) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $PortToCheck)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}
if (Test-PortInUse $Port) {
    Write-Host "[x] 端口 $Port 已被占用：服务可能已在运行，或换个端口：" -ForegroundColor Red
    Write-Host '    $env:PORT=8011; powershell -ExecutionPolicy Bypass -File scripts\start.ps1'
    exit 1
}
Write-Host "[v] 端口 $Port 可用"

Write-Host ''
Write-Host "启动服务：http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host '按 Ctrl+C 停止'
Write-Host ''

& python -m uvicorn server.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
