[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$DistributionPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Fa-f0-9 ]{40}$')]
    [string]$CertificateThumbprint,

    [ValidateSet('CurrentUser', 'LocalMachine')]
    [string]$CertificateStoreLocation = 'CurrentUser',

    [string]$TimestampUrl = 'http://timestamp.digicert.com',

    [switch]$ForceResign,

    [string]$ReportPath = 'work\release-gate\signing-report.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-SignTool {
    $command = Get-Command 'signtool.exe' -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsRoot) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($null -ne $candidate) {
            return $candidate
        }
    }
    throw '找不到 signtool.exe。请安装 Windows 10/11 SDK，并在 Developer PowerShell 中重试。'
}

function Get-ReleaseBinaries([string]$RootPath) {
    return @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -File |
            Where-Object { $_.Extension.ToLowerInvariant() -in @('.exe', '.dll', '.pyd') } |
            Sort-Object FullName
    )
}

$distribution = (Resolve-Path -LiteralPath $DistributionPath).Path
if (-not (Test-Path -LiteralPath $distribution -PathType Container)) {
    throw "分发目录不存在：$DistributionPath"
}
$mainExecutable = Join-Path $distribution 'PowertradeCrawler.exe'
if (-not (Test-Path -LiteralPath $mainExecutable -PathType Leaf)) {
    throw "分发目录中缺少 PowertradeCrawler.exe：$distribution"
}

$thumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
$certificatePath = "Cert:\$CertificateStoreLocation\My\$thumbprint"
$certificate = Get-Item -LiteralPath $certificatePath -ErrorAction Stop
if (-not $certificate.HasPrivateKey) {
    throw '代码签名证书没有可用私钥。'
}
if ($certificate.NotAfter -le (Get-Date)) {
    throw "代码签名证书已过期：$($certificate.NotAfter.ToString('s'))"
}
if ($certificate.PublicKey.Oid.Value -notin @('1.2.840.113549.1.1.1', '1.2.840.113549.1.1.10')) {
    throw 'Smart App Control 当前要求 RSA 证书；所选证书不是 RSA。'
}
$codeSigningOid = '1.3.6.1.5.5.7.3.3'
$hasCodeSigningEku = @($certificate.EnhancedKeyUsageList | Where-Object { $_.ObjectId.Value -eq $codeSigningOid }).Count -gt 0
if (-not $hasCodeSigningEku) {
    throw '所选证书不包含 Code Signing EKU (1.3.6.1.5.5.7.3.3)。'
}

$signTool = Resolve-SignTool
$binaries = Get-ReleaseBinaries $distribution
if ($binaries.Count -eq 0) {
    throw "分发目录中没有可签名二进制文件：$distribution"
}

$signed = [System.Collections.Generic.List[string]]::new()
$skipped = [System.Collections.Generic.List[string]]::new()
foreach ($binary in $binaries) {
    $existing = Get-AuthenticodeSignature -LiteralPath $binary.FullName
    $existingCertificate = $existing.SignerCertificate
    $existingIsRsa = (
        $null -ne $existingCertificate -and
        $existingCertificate.PublicKey.Oid.Value -in @(
            '1.2.840.113549.1.1.1', '1.2.840.113549.1.1.10'
        )
    )
    $isMainExecutable = $binary.FullName -eq $mainExecutable
    $mainHasExpectedCertificate = (
        $isMainExecutable -and
        $null -ne $existingCertificate -and
        $existingCertificate.Thumbprint -eq $thumbprint
    )
    $canPreserveExisting = (
        $existing.Status -eq 'Valid' -and
        $existingIsRsa -and
        (-not $isMainExecutable -or $mainHasExpectedCertificate)
    )
    if (-not $ForceResign -and $canPreserveExisting) {
        $skipped.Add($binary.FullName)
        continue
    }
    if (-not $PSCmdlet.ShouldProcess($binary.FullName, 'Authenticode SHA-256 签名并 RFC 3161 时间戳')) {
        continue
    }
    $arguments = @(
        'sign', '/sha1', $thumbprint, '/s', 'My', '/fd', 'SHA256',
        '/tr', $TimestampUrl, '/td', 'SHA256', '/v'
    )
    if ($CertificateStoreLocation -eq 'LocalMachine') {
        $arguments += '/sm'
    }
    $arguments += $binary.FullName
    & $signTool @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "签名失败（exit $LASTEXITCODE）：$($binary.FullName)"
    }
    $signed.Add($binary.FullName)
}

$reportAbsolute = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $ReportPath))
$reportDirectory = Split-Path -Parent $reportAbsolute
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$report = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    distribution_path = $distribution
    signer_subject = $certificate.Subject
    signer_issuer = $certificate.Issuer
    certificate_thumbprint = $thumbprint
    certificate_not_after = $certificate.NotAfter.ToUniversalTime().ToString('o')
    timestamp_url = $TimestampUrl
    binary_count = $binaries.Count
    signed_count = $signed.Count
    preserved_valid_vendor_signatures = $skipped.Count
    signed_files = @($signed)
}
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportAbsolute -Encoding UTF8
Write-Host "签名完成：新增签名 $($signed.Count) 个，保留已有有效签名 $($skipped.Count) 个。"
Write-Host "报告：$reportAbsolute"
