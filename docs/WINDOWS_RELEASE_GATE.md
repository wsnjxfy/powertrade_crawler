# Windows 签名与干净机发布门禁

本门禁用于正式 Windows 交付。它把代码质量、冻结构建、全二进制 Authenticode 签名、离线
Windows Sandbox 验收和 Smart App Control 人工验收串成同一条发布链。任何一步失败，都不应
发布该目录。

## 1. 可信签名的前置条件

1. 使用受信任提供商签发的 **RSA 代码签名证书**，并包含 Code Signing EKU
   `1.3.6.1.5.5.7.3.3`。Smart App Control 当前不接受 ECC 签名，也不会把普通自签名证书
   当作公开交付的可信发布者。参见 Microsoft 的
   [Smart App Control 签名说明](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)。
2. 把证书和私钥安全安装到发布机的 `CurrentUser\My` 或 `LocalMachine\My` 证书库。项目脚本
   只接收证书指纹，不接收 PFX 密码，不复制或导出私钥。
3. 安装 Windows 10/11 SDK，使 `signtool.exe` 可用。脚本会优先读取 `PATH`，否则查找最新的
   Windows Kits x64 版本。SignTool 的签名、验证和时间戳行为见
   [Microsoft SignTool 文档](https://learn.microsoft.com/en-us/dotnet/framework/tools/signtool-exe)。
4. 发布机需要能访问所配置的 RFC 3161 时间戳服务；最终运行验收则保持离线。
5. 启用 Windows Sandbox 可选功能。Sandbox 门禁会关闭网络、vGPU 和剪贴板，只映射一个
   专用验收目录。配置依据见
   [Windows Sandbox 配置示例](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-sample-configuration)。

私钥、PFX、密码、签名服务令牌和证书备份禁止进入源码、`.env`、`.auth`、数据库、日志、
截图、`work/` 报告或 Git。

## 2. 一键正式门禁

在主项目根目录、干净的 `main` 工作区中运行：

```powershell
.\scripts\release_windows_gate.ps1 `
  -CertificateThumbprint "你的 40 位证书指纹" `
  -ExpectedSignerSubjectPattern "你的公司或发布者名称"
```

脚本依次执行：

1. 要求 Git 工作区干净；
2. 运行全部 Pytest、Ruff、Python 编译和 `git diff --check`；
3. 校验内置 BGE 模型文件清单和 SHA-256；
4. 执行 PyInstaller `--clean` 构建，并再次排除 `.auth` 和凭据文件；
5. 对分发目录内尚无有效签名的所有 `.exe`、`.dll`、`.pyd` 做 SHA-256 Authenticode
   签名和 RFC 3161 时间戳；已有有效的第三方发布者签名会保留；
6. 验证每个可执行二进制都有有效 RSA 签名，主程序有时间戳且签名者符合预期；
7. 在网络关闭的全新 Windows Sandbox 中复制并运行分发目录，验收中文空格路径、首次启动、
   演示数据库不覆盖、冻结态混合 RAG 和全部 8 个 GUI 顶层页面。

临时验证脏工作区时可以显式增加 `-AllowDirtyWorktree`，但使用该开关得到的结果不能作为正式
发布证据。

## 3. 可单独运行的步骤

```powershell
# 对未签名/无效签名的二进制签名
.\scripts\sign_windows_release.ps1 `
  -DistributionPath .\dist\PowertradeCrawler `
  -CertificateThumbprint "证书指纹"

# 验证全目录签名；任一 EXE/DLL/PYD 未签名、签名无效或不是 RSA 都失败
.\scripts\verify_windows_signature.ps1 `
  -DistributionPath .\dist\PowertradeCrawler `
  -ExpectedSignerSubjectPattern "发布者名称"

# 启动实际 Windows Sandbox 离线验收
.\scripts\run_windows_sandbox_gate.ps1 `
  -DistributionPath .\dist\PowertradeCrawler `
  -ExpectedSignerSubjectPattern "发布者名称"
```

机器可读报告保存在 `work/release-gate/`。Sandbox 的独立证据目录包含结果 JSON、冻结命令输出
和失败详情。`work/` 已被 Git 忽略。

## 4. 自动化干净机的硬性断言

- 分发目录不超过 320 MB，且不含 `.auth` 或 `credentials.json`；
- PyInstaller 源分发目录根部不得出现运行生成的 `.env`、`data/` 或 `configs/`；验收程序只能
  在复制出的临时目录启动，避免污染待签名/待压缩工件；
- 程序从包含中文和空格的全新可写路径运行；
- Sandbox 的 `<Networking>` 为 `Disable`，同时设置 Hugging Face/Transformers 离线环境；
- 首次命令必须从包内复制 `data/powertrade.db`，RAG 状态必须是 `ready` 且模型可用；
- 检索“绿证交易”必须返回 `retrieval_mode=hybrid`、至少一个向量命中且无降级告警；
- RAG 查询和第二次 GUI 启动前后的数据库 SHA-256 必须相同；
- `--acceptance-gui-smoke` 必须成功构建并切换全部 8 个顶层页面；
- 当前 Windows 账户之外不需要 API Key、LLM Key、管理员权限或网络。

## 5. Smart App Control / 企业应用控制最终人工门禁

Windows Sandbox 能提供新的用户环境和真实断网，但不能替代所有企业 App Control 策略。正式
发布还要在一台**全新安装且 Smart App Control 处于强制模式**的 Windows 11 验收机上执行：

1. 从最终发布压缩包解压到中文空格路径，不安装开发工具、不导入项目私有证书；
2. 确认主程序属性中的发布者、签名和时间戳正确；
3. 双击启动，依次打开 8 个顶层页面、知识库窗口，执行一次离线 RAG 查询后退出；
4. 再次启动，确认已有数据库未被覆盖；
5. 检查 `Microsoft-Windows-CodeIntegrity/Operational`，不得出现与分发目录有关的阻止事件；
   Microsoft 文档说明评估事件为 3076、强制阻止事件为 3077，且应覆盖所有会加载的二进制
   和功能路径。参见
   [Smart App Control 测试指南](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control)。

Smart App Control 允许受信任 CA 签发的已签名应用，说明见
[官方概览](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/overview)。企业自定义
App Control 策略仍可能要求 IT 将发布者、证书或文件规则加入允许列表；“签名有效”并不自动
覆盖组织自己的拒绝策略。

只有自动一键门禁、实际 Smart App Control 验收和目标企业策略验收三者都通过，才可标记为
正式 Windows 发布。
