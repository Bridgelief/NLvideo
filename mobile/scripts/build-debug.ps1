#Requires -Version 5.1
<#
.SYNOPSIS
  一键构建「拍摄指导」Android debug APK（使用仓库内便携工具链 _tools/）。

.DESCRIPTION
  流程：npx cap sync android -> python scripts/apply-mobile-config.py（写回 API 地址 + CA）
       -> gradlew.bat assembleDebug -> 拷贝 APK 到 mobile/shooting-guide-debug.apk
        -> apksigner verify。

.PARAMETER ApiBase
  APK 内指向的后端 HTTPS 地址，默认 https://172.20.10.7:8443。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File D:\videoshoot\mobile\scripts\build-debug.ps1
  powershell -ExecutionPolicy Bypass -File .\build-debug.ps1 -ApiBase 192.168.1.23:8443
#>
param(
    [string]$ApiBase = "https://172.20.10.7:8443"
)

$ErrorActionPreference = "Stop"
$Mobile = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Repo = (Resolve-Path (Join-Path $Mobile "..")).Path
$Tools = Join-Path $Repo "_tools"

# --- 便携工具链路径（不存在就明确报错，不去动系统环境） ---
$NodeDir = Join-Path $Tools "node-v20.18.0-win-x64"
$JdkDir = Join-Path $Tools "jdk-17.0.21.1+2"
$SdkDir = Join-Path $Tools "android-sdk"
$GradleHome = Join-Path $Tools "gradle-home"

foreach ($p in @($NodeDir, $JdkDir, $SdkDir)) {
    if (-not (Test-Path $p)) { throw "缺少便携工具链：$p（先按 mobile/README.md §8 安装）" }
}

$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $SdkDir
$env:ANDROID_SDK_ROOT = $SdkDir
$env:GRADLE_USER_HOME = $GradleHome
$env:PATH = "$NodeDir;$JdkDir\bin;$env:PATH"

Push-Location $Mobile
try {
    Write-Host "[1/4] npx cap sync android" -ForegroundColor Cyan
    & npx cap sync android
    if ($LASTEXITCODE -ne 0) { throw "cap sync 失败（exit $LASTEXITCODE）" }

    Write-Host "[2/4] 写回 app-config.js / CA" -ForegroundColor Cyan
    & python (Join-Path $Mobile "scripts\apply-mobile-config.py") $ApiBase
    if ($LASTEXITCODE -ne 0) { throw "apply-mobile-config.py 失败（exit $LASTEXITCODE）" }

    Write-Host "[3/4] gradlew assembleDebug" -ForegroundColor Cyan
    Push-Location (Join-Path $Mobile "android")
    try {
        & .\gradlew.bat --no-daemon assembleDebug
        if ($LASTEXITCODE -ne 0) { throw "gradle 构建失败（exit $LASTEXITCODE）" }
    } finally { Pop-Location }

    $apk = Join-Path $Mobile "android\app\build\outputs\apk\debug\app-debug.apk"
    if (-not (Test-Path $apk)) { throw "没找到构建产物：$apk" }
    $out = Join-Path $Mobile "shooting-guide-debug.apk"
    Copy-Item -Force $apk $out
    Write-Host "[4/4] APK -> $out ($([math]::Round((Get-Item $out).Length/1MB,2)) MB)" -ForegroundColor Green

    $apksigner = Join-Path $SdkDir "build-tools\34.0.0\apksigner.bat"
    if (Test-Path $apksigner) {
        Write-Host "--- apksigner verify --verbose ---" -ForegroundColor Cyan
        & $apksigner verify --verbose $out
    }
} finally {
    Pop-Location
}
