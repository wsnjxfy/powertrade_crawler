[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedSignerSubjectPattern,

    [ValidateSet('CurrentUser', 'LocalMachine')]
    [string]$CertificateStoreLocation = 'CurrentUser',

    [string]$TimestampUrl = 'http://timestamp.digicert.com',

    [switch]$AllowDirtyWorktree
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$distribution = Join-Path $projectRoot 'dist\PowertradeCrawler'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "缺少项目虚拟环境 Python：$python"
}

Push-Location $projectRoot
try {
    if (-not $AllowDirtyWorktree) {
        $changes = @(git status --porcelain=v1)
        if ($LASTEXITCODE -ne 0) {
            throw '无法读取 Git 状态。'
        }
        if ($changes.Count -gt 0) {
            throw "发布门禁要求干净工作区。请先审阅并提交改动，或仅在临时验证时显式使用 -AllowDirtyWorktree。"
        }
    }

    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Pytest 发布门禁失败。' }
    & $python -m ruff check src tests scripts desktop_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Ruff 发布门禁失败。' }
    & $python -m compileall -q src scripts desktop_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Python 编译发布门禁失败。' }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'git diff --check 发布门禁失败。' }
    & $python scripts\prepare_rag_model.py --verify-only
    if ($LASTEXITCODE -ne 0) { throw '离线 RAG 模型清单或 SHA-256 校验失败。' }

    & $python -m PyInstaller --noconfirm --clean PowertradeCrawler.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 干净构建失败。' }
    if (-not (Test-Path -LiteralPath (Join-Path $distribution 'PowertradeCrawler.exe'))) {
        throw 'PyInstaller 没有生成预期分发目录。'
    }
    $mutableRootArtifacts = @(
        '.env', '.auth', 'data', 'configs' |
            ForEach-Object { Join-Path $distribution $_ } |
            Where-Object { Test-Path -LiteralPath $_ }
    )
    if ($mutableRootArtifacts.Count -gt 0) {
        throw "干净构建目录包含运行态文件：$($mutableRootArtifacts -join ', ')"
    }
    $forbidden = @(
        Get-ChildItem -LiteralPath $distribution -Recurse -Force |
            Where-Object { $_.Name -in @('.auth', 'credentials.json') }
    )
    if ($forbidden.Count -gt 0) {
        throw 'PyInstaller 分发目录包含 .auth 或 credentials.json。'
    }

    & (Join-Path $PSScriptRoot 'sign_windows_release.ps1') `
        -DistributionPath $distribution `
        -CertificateThumbprint $CertificateThumbprint `
        -CertificateStoreLocation $CertificateStoreLocation `
        -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw 'Windows 代码签名步骤失败。' }

    & (Join-Path $PSScriptRoot 'verify_windows_signature.ps1') `
        -DistributionPath $distribution `
        -ExpectedSignerSubjectPattern $ExpectedSignerSubjectPattern
    if ($LASTEXITCODE -ne 0) { throw 'Windows 签名验证门禁失败。' }

    & (Join-Path $PSScriptRoot 'run_windows_sandbox_gate.ps1') `
        -DistributionPath $distribution `
        -ExpectedSignerSubjectPattern $ExpectedSignerSubjectPattern
    if ($LASTEXITCODE -ne 0) { throw 'Windows Sandbox 干净机门禁失败。' }

    Write-Host 'Windows 最终发布门禁全部通过，可以生成发布压缩包和校验清单。'
}
finally {
    Pop-Location
}
