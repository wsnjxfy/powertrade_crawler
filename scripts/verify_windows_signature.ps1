[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DistributionPath,

    [string]$ExpectedSignerSubjectPattern = '',

    [string]$ReportPath = 'work\release-gate\signature-verification.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$distribution = (Resolve-Path -LiteralPath $DistributionPath).Path
$mainExecutable = Join-Path $distribution 'PowertradeCrawler.exe'
if (-not (Test-Path -LiteralPath $mainExecutable -PathType Leaf)) {
    throw "分发目录中缺少 PowertradeCrawler.exe：$distribution"
}

$binaries = @(
    Get-ChildItem -LiteralPath $distribution -Recurse -File |
        Where-Object { $_.Extension.ToLowerInvariant() -in @('.exe', '.dll', '.pyd') } |
        Sort-Object FullName
)
if ($binaries.Count -eq 0) {
    throw "分发目录中没有可验证二进制文件：$distribution"
}

$results = [System.Collections.Generic.List[object]]::new()
$failures = [System.Collections.Generic.List[string]]::new()
foreach ($binary in $binaries) {
    $signature = Get-AuthenticodeSignature -LiteralPath $binary.FullName
    $certificate = $signature.SignerCertificate
    $subject = if ($null -ne $certificate) { $certificate.Subject } else { $null }
    $publicKeyOid = if ($null -ne $certificate) { $certificate.PublicKey.Oid.Value } else { $null }
    $isRsa = $publicKeyOid -in @('1.2.840.113549.1.1.1', '1.2.840.113549.1.1.10')
    $relativePath = $binary.FullName.Substring($distribution.Length).TrimStart('\')
    $isMain = $binary.FullName -eq $mainExecutable
    $fileFailures = [System.Collections.Generic.List[string]]::new()
    if ($signature.Status -ne 'Valid') {
        $fileFailures.Add("Authenticode 状态为 $($signature.Status)：$relativePath")
    }
    if (-not $isRsa) {
        $fileFailures.Add("签名证书不是 RSA：$relativePath")
    }
    if ($isMain -and $null -eq $signature.TimeStamperCertificate) {
        $fileFailures.Add('主程序缺少可信时间戳。')
    }
    if (
        $isMain -and
        $ExpectedSignerSubjectPattern -and
        ($null -eq $subject -or $subject -notmatch $ExpectedSignerSubjectPattern)
    ) {
        $fileFailures.Add("主程序签名者不符合预期：$subject")
    }
    foreach ($failure in $fileFailures) {
        $failures.Add($failure)
    }
    $results.Add([ordered]@{
        path = $relativePath
        status = [string]$signature.Status
        status_message = $signature.StatusMessage
        signer_subject = $subject
        signer_thumbprint = if ($null -ne $certificate) { $certificate.Thumbprint } else { $null }
        rsa = $isRsa
        timestamped = $null -ne $signature.TimeStamperCertificate
        sha256 = (Get-FileHash -LiteralPath $binary.FullName -Algorithm SHA256).Hash
        failures = @($fileFailures)
    })
}

$reportAbsolute = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $ReportPath))
$reportDirectory = Split-Path -Parent $reportAbsolute
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$report = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    distribution_path = $distribution
    binary_count = $binaries.Count
    valid = $failures.Count -eq 0
    expected_main_signer_pattern = $ExpectedSignerSubjectPattern
    failures = @($failures)
    binaries = @($results)
}
$report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportAbsolute -Encoding UTF8

if ($failures.Count -gt 0) {
    $preview = @($failures | Select-Object -First 12) -join [Environment]::NewLine
    throw "签名门禁失败，共 $($failures.Count) 项：`n$preview`n报告：$reportAbsolute"
}
Write-Host "签名门禁通过：$($binaries.Count) 个 EXE/DLL/PYD 均为有效 RSA Authenticode 签名。"
Write-Host "报告：$reportAbsolute"
