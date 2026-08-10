[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DistributionPath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedSignerSubjectPattern,

    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$distribution = (Resolve-Path -LiteralPath $DistributionPath).Path
$verifyScript = Join-Path $PSScriptRoot 'verify_windows_signature.ps1'
& $verifyScript -DistributionPath $distribution -ExpectedSignerSubjectPattern $ExpectedSignerSubjectPattern
if ($LASTEXITCODE -ne 0) {
    throw '进入干净机前的签名门禁失败。'
}

$sandboxExecutable = Join-Path $env:SystemRoot 'System32\WindowsSandbox.exe'
if (-not (Test-Path -LiteralPath $sandboxExecutable -PathType Leaf)) {
    throw '当前机器未安装 Windows Sandbox。请先启用 Containers-DisposableClientVM 可选功能。'
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stagingRoot = Join-Path $projectRoot "work\release-gate\sandbox-$timestamp-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$inputPath = Join-Path $stagingRoot 'input\PowertradeCrawler'
$outputPath = Join-Path $stagingRoot 'output'
New-Item -ItemType Directory -Path (Split-Path -Parent $inputPath) -Force | Out-Null
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
Copy-Item -LiteralPath $distribution -Destination $inputPath -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'clean_machine_acceptance.ps1') -Destination $stagingRoot

$guestWrapper = @'
$ErrorActionPreference = 'Stop'
$donePath = 'C:\PowertradeGate\output\passed.txt'
$failedPath = 'C:\PowertradeGate\output\failed.txt'
try {
    & 'C:\PowertradeGate\clean_machine_acceptance.ps1' `
        -DistributionPath 'C:\PowertradeGate\input\PowertradeCrawler' `
        -AcceptanceRoot 'C:\Users\WDAGUtilityAccount\Desktop\电力 软件 干净机验收' `
        -ReportPath 'C:\PowertradeGate\output\clean-machine-report.json'
    'passed' | Set-Content -LiteralPath $donePath -Encoding UTF8
}
catch {
    ($_ | Out-String) | Set-Content -LiteralPath $failedPath -Encoding UTF8
}
finally {
    Start-Sleep -Seconds 2
    shutdown.exe /s /t 0
}
'@
$guestWrapperPath = Join-Path $stagingRoot 'run-guest.ps1'
$guestWrapper | Set-Content -LiteralPath $guestWrapperPath -Encoding UTF8

$escapedStagingRoot = [System.Security.SecurityElement]::Escape($stagingRoot)
$configuration = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <ProtectedClient>Enable</ProtectedClient>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$escapedStagingRoot</HostFolder>
      <SandboxFolder>C:\PowertradeGate</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\PowertradeGate\run-guest.ps1</Command>
  </LogonCommand>
</Configuration>
"@
$configurationPath = Join-Path $stagingRoot 'PowertradeCrawler-offline-gate.wsb'
$configuration | Set-Content -LiteralPath $configurationPath -Encoding UTF8

$quotedConfigurationPath = '"' + $configurationPath + '"'
$sandboxProcess = Start-Process `
    -FilePath $sandboxExecutable `
    -ArgumentList @($quotedConfigurationPath) `
    -WindowStyle Hidden `
    -PassThru
$passedPath = Join-Path $outputPath 'passed.txt'
$failedPath = Join-Path $outputPath 'failed.txt'
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $passedPath -PathType Leaf) {
        Write-Host "Windows Sandbox 干净机门禁通过。报告目录：$outputPath"
        exit 0
    }
    if (Test-Path -LiteralPath $failedPath -PathType Leaf) {
        $failure = Get-Content -LiteralPath $failedPath -Raw
        throw "Windows Sandbox 干净机门禁失败：`n$failure`n证据目录：$outputPath"
    }
    if ($sandboxProcess.HasExited) {
        throw "Windows Sandbox 在生成结果前退出（exit $($sandboxProcess.ExitCode)）。证据目录：$outputPath"
    }
    Start-Sleep -Seconds 2
    $sandboxProcess.Refresh()
}

if (-not $sandboxProcess.HasExited) {
    Stop-Process -Id $sandboxProcess.Id -Force -ErrorAction SilentlyContinue
}
throw "Windows Sandbox 干净机门禁超时（$TimeoutSeconds 秒）。证据目录：$outputPath"
