# Powertrade Crawler 本地知识库指南

## 1. 功能范围

多数据源 Agent 使用本地混合 RAG 回答政策、规则、概念、文章正文、数据集说明、
字段和口径问题。数值、排名、趋势和跨来源比较仍由原有确定性数据工具完成；
“最新新闻”仍由发布日期排序工具完成。

知识库只读取以下白名单公开资料：

- 广州电力交易中心已采集文章；
- GridStatus 本地数据集目录；
- ENTSO-E 和 Elexon 受控双语请求定义；
- 多数据源 Agent 的受控数据目录口径，包括 Elecheck 数据集定义。

不会索引业务价格/负荷/发电量时序、`.auth`、环境文件、会话、审批、任务参数、
导出文件、日志或用户文件。它没有网页即时抓取、PDF/OCR、目录扫描、SQL、Shell、
凭据修改或业务数据写入权限。

## 2. 本地运行方式

- 向量模型：`BAAI/bge-small-zh-v1.5`，512 维；
- 运行时：`fastembed==0.8.0` + `onnxruntime==1.28.0`（CPU）；
- 存储：现有 SQLite 中的可重建派生表；
- 检索：归一化向量点积 + SQLite FTS5 `trigram` + RRF；
- 网络：运行时使用 `local_files_only=True`，不下载模型、不调用在线 Embedding API。

向量与全文各取最多 40 个候选，默认 RRF 权重为 0.65/0.35；融合后会对查询中明确的
数据集英文标识和完整年份/周次/日期做确定性的标题精排，避免精确对象被相似主题淹没。
解释类问题还会优先展示规则、制度、办法、问答和政策解读，并降低纯行情周报/月报的
排序，避免用量价快报回答制度问题。
同一文档最多返回
两个分块，最多返回 8 条，总上下文不超过 3600 个字符。向量模型不可用时自动降级
为全文检索，并在答案里标出原因。

## 3. 管理入口

多数据源 Agent 页面状态区的“知识库”按钮会打开可调整大小的管理窗口。窗口显示
模型与索引版本、文档/分块数量、各来源统计、进度和最后错误，并提供增量更新、完整
重建、停止更新和刷新状态。

已有用户数据库会在 GUI 空闲后后台建立索引，不阻塞启动。完整重建采用“新代次构建、
原子切换、清理旧代次”；失败、停止或回滚不会修改业务表，也不会破坏旧索引。增量更新
会复用内容哈希未变化的现有向量。

## 4. CLI

```powershell
powertrade market-agent rag status --json
powertrade market-agent rag update
powertrade market-agent rag update --source gzpec
powertrade market-agent rag rebuild
powertrade market-agent rag search "LOLP 是什么意思" --top-k 6 --json
powertrade market-agent rag eval --json
```

`update --source` 用于声明本次重点来源；为保证代次完整性，程序仍会复用其余白名单来源
组成完整新代次，不会产生缺来源的活动索引。

## 5. 引用与安全

检索结果统一标记为“不可信外部内容”。模型不得执行正文里的任何指令。程序只接受
工具实际返回的稳定 `K-...` 引用 ID，并据此生成 `knowledge_citations`；模型虚构的 ID
会被删除。模型没有提供有效引用时，程序退回相关资料列表，不输出未经引用支持的知识
结论。无足够相关结果时明确返回空结果。

## 6. 模型准备与发布

```powershell
.\.venv\Scripts\python.exe scripts\prepare_rag_model.py
.\.venv\Scripts\python.exe scripts\prepare_rag_model.py --verify-only
```

模型写入 Git 忽略的 `work/rag-model`。脚本通过 FastEmbed 下载优化 ONNX 模型，生成
逐文件 SHA-256 清单；PyInstaller 只打包已准备并有清单的模型。运行时再次验证清单后
才加载。第三方许可见 `docs/THIRD_PARTY_NOTICES_RAG.md`。

演示数据库 `resources/initial/powertrade.initial.db` 已预建完整索引。首次启动仍只在用户
数据库不存在时复制演示库，绝不会覆盖已有数据库。

## 7. 故障处理

- “全文降级”：检查分发目录的 `rag_model` 和模型清单，重新安装完整分发包；全文检索
  仍可用。
- “尚未建立”：打开知识库窗口执行增量更新。
- “构建失败”：查看窗口里的最后错误；旧索引仍可用。只读数据库需要把数据目录迁移
  到当前用户可写位置。
- “没有相关资料”：调整关键词或来源/日期过滤；程序不会用低相关文本拼凑答案。
