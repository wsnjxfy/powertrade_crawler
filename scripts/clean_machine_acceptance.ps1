[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DistributionPath,

    [Parameter(Mandatory = $true)]
    [string]$AcceptanceRoot,

    [Parameter(Mandatory = $true)]
    [string]$ReportPath,

    [int]$MaximumDistributionSizeMB = 320
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-FrozenCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Name
    )
    $outputPath = Join-Path $script:logsPath "$Name.json"
    $errorPath = Join-Path $script:logsPath "$Name.stderr.txt"
    Remove-Item -LiteralPath $outputPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $errorPath -Force -ErrorAction SilentlyContinue
    $env:POWERTRADE_HEADLESS_OUTPUT_FILE = $outputPath
    $env:POWERTRADE_HEADLESS_ERROR_FILE = $errorPath
    try {
        $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -Wait -PassThru
    }
    finally {
        Remove-Item Env:POWERTRADE_HEADLESS_OUTPUT_FILE -ErrorAction SilentlyContinue
        Remove-Item Env:POWERTRADE_HEADLESS_ERROR_FILE -ErrorAction SilentlyContinue
    }
    if ($process.ExitCode -ne 0) {
        $errorText = if (Test-Path -LiteralPath $errorPath) {
            Get-Content -LiteralPath $errorPath -Raw
        }
        else {
            '未生成错误输出。'
        }
        throw "冻结命令 $Name 失败（exit $($process.ExitCode)）：$errorText"
    }
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "冻结命令 $Name 没有生成 JSON 输出：$outputPath"
    }
    $raw = Get-Content -LiteralPath $outputPath -Raw
    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        throw "冻结命令 $Name 输出不是有效 JSON：$raw"
    }
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

$distribution = (Resolve-Path -LiteralPath $DistributionPath).Path
$mutableRootArtifacts = @(
    '.env', '.auth', 'data', 'configs' |
        ForEach-Object { Join-Path $distribution $_ } |
        Where-Object { Test-Path -LiteralPath $_ }
)
Assert-Condition ($mutableRootArtifacts.Count -eq 0) (
    "分发源目录已被运行态文件污染，必须重新执行 PyInstaller --clean：$($mutableRootArtifacts -join ', ')"
)
$acceptanceAbsolute = [System.IO.Path]::GetFullPath($AcceptanceRoot)
$acceptanceParent = Split-Path -Parent $acceptanceAbsolute
Assert-Condition ($acceptanceAbsolute -ne [System.IO.Path]::GetPathRoot($acceptanceAbsolute)) '验收目录不能是磁盘根目录。'
Assert-Condition ($acceptanceAbsolute -match '[^\x00-\x7F]') '验收目录必须包含中文字符。'
Assert-Condition ($acceptanceAbsolute.Contains(' ')) '验收目录必须包含空格。'
Assert-Condition (-not (Test-Path -LiteralPath $acceptanceAbsolute)) "验收目录必须是全新路径：$acceptanceAbsolute"

New-Item -ItemType Directory -Path $acceptanceParent -Force | Out-Null
$runtimePath = Join-Path $acceptanceAbsolute 'Powertrade Crawler 离线版'
Copy-Item -LiteralPath $distribution -Destination $runtimePath -Recurse
$script:logsPath = Join-Path $acceptanceAbsolute '验收 日志'
New-Item -ItemType Directory -Path $script:logsPath -Force | Out-Null

$executable = Join-Path $runtimePath 'PowertradeCrawler.exe'
Assert-Condition (Test-Path -LiteralPath $executable -PathType Leaf) "缺少主程序：$executable"
$authArtifacts = @(
    Get-ChildItem -LiteralPath $runtimePath -Recurse -Force |
        Where-Object { $_.Name -in @('.auth', 'credentials.json') }
)
Assert-Condition ($authArtifacts.Count -eq 0) '分发包携带了 .auth 或 credentials.json。'

$distributionBytes = (
    Get-ChildItem -LiteralPath $runtimePath -Recurse -File |
        Measure-Object -Property Length -Sum
).Sum
$maximumBytes = [int64]$MaximumDistributionSizeMB * 1MB
Assert-Condition ($distributionBytes -le $maximumBytes) (
    "分发目录超过大小门禁：$([Math]::Round($distributionBytes / 1MB, 2)) MB > $MaximumDistributionSizeMB MB"
)

$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:POWERTRADE_DISABLE_NETWORK = '1'
try {
    $status = Invoke-FrozenCommand -Executable $executable -Arguments @(
        '--headless', 'market-agent', 'rag', 'status', '--json'
    ) -Name '01-rag-status'
    $databasePath = Join-Path $runtimePath 'data\powertrade.db'
    Assert-Condition (Test-Path -LiteralPath $databasePath -PathType Leaf) '首次启动没有复制演示数据库。'
    Assert-Condition ([bool]$status.ready) '冻结态知识库未就绪。'
    Assert-Condition ($status.status -eq 'ready') "冻结态知识库状态不是 ready：$($status.status)"
    Assert-Condition ([bool]$status.model_available) "冻结态向量模型不可用：$($status.model_error)"
    Assert-Condition ([int]$status.document_count -gt 0) '冻结态知识库文档数为 0。'
    Assert-Condition ([int]$status.chunk_count -gt 0) '冻结态知识库分块数为 0。'
    $databaseHashBefore = (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash

    $search = Invoke-FrozenCommand -Executable $executable -Arguments @(
        '--headless', 'market-agent', 'rag', 'search', '绿证交易', '--top-k', '3', '--json'
    ) -Name '02-rag-search'
    Assert-Condition ($search.retrieval_mode -eq 'hybrid') (
        "冻结态 RAG 未进入 hybrid，而是 $($search.retrieval_mode)。"
    )
    Assert-Condition (@($search.hits).Count -gt 0) '冻结态 RAG 没有返回命中。'
    Assert-Condition (@($search.warnings).Count -eq 0) (
        "冻结态 RAG 出现降级告警：$(@($search.warnings) -join '; ')"
    )
    $vectorHits = @(
        $search.hits | Where-Object { @($_.retrieval_channels) -contains 'vector' }
    )
    Assert-Condition ($vectorHits.Count -gt 0) '冻结态结果没有任何向量召回命中。'
    $databaseHashAfterSearch = (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash
    Assert-Condition ($databaseHashBefore -eq $databaseHashAfterSearch) (
        '只读 RAG 查询改变了首次启动数据库，或重复启动覆盖了已有数据库。'
    )

    $guiSmoke = Invoke-FrozenCommand -Executable $executable -Arguments @(
        '--acceptance-gui-smoke'
    ) -Name '03-gui-smoke'
    Assert-Condition ($guiSmoke.gui_smoke -eq 'passed') '冻结态 GUI 冒烟未通过。'
    Assert-Condition ([int]$guiSmoke.pages -eq 8) '冻结态 GUI 没有构建全部 8 个顶层页面。'
    $databaseHashAfterGui = (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash
    Assert-Condition ($databaseHashAfterSearch -eq $databaseHashAfterGui) (
        '重复 GUI 启动覆盖或改写了已有演示数据库。'
    )
}
finally {
    foreach ($name in @(
        'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE',
        'POWERTRADE_DISABLE_NETWORK'
    )) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}

$reportAbsolute = [System.IO.Path]::GetFullPath($ReportPath)
$reportDirectory = Split-Path -Parent $reportAbsolute
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$report = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    passed = $true
    distribution_source = $distribution
    acceptance_path = $runtimePath
    offline_environment = $true
    distribution_size_mb = [Math]::Round($distributionBytes / 1MB, 2)
    database_sha256 = $databaseHashAfterGui
    rag_status = [ordered]@{
        status = $status.status
        document_count = $status.document_count
        chunk_count = $status.chunk_count
        model_available = $status.model_available
    }
    rag_search = [ordered]@{
        retrieval_mode = $search.retrieval_mode
        hit_count = @($search.hits).Count
        warning_count = @($search.warnings).Count
    }
    gui_smoke = $guiSmoke
}
$report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportAbsolute -Encoding UTF8
Write-Host "干净机验收通过：中文空格路径、离线首次启动、冻结态 hybrid RAG、GUI 和不覆盖检查均通过。"
Write-Host "报告：$reportAbsolute"
