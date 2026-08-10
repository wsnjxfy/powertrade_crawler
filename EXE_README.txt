Powertrade Crawler 使用说明

一、启动方式
1. 解压整个文件夹，不要直接在压缩包里运行。
2. 双击 PowertradeCrawler.exe。
3. 首次启动会自动创建 .env、data 目录和本地数据库结构。

二、鉴权文件
1. 新用户优先在软件左侧“API 配置”页面按向导配置，不需要手工编辑文件。
2. 电力网站 API key、token 和 Authorization 统一保存在 exe 同级目录的：
   .auth\credentials.json
3. GridStatus、ENTSO-E、Elecheck 仅在对应来源在线采集时需要凭据；Elexon 和广州电力交易中心无需 Key。
4. .env 只保存数据库、超时和请求间隔等非敏感配置，不保存凭据。
5. 两套 Agent 只连接本机免费 LLM 网关；上游 LLM Key 不保存在本软件目录中。
6. 页面只显示“已配置 / 待配置”，不会回显已经保存的真实凭据。
7. 如果采集返回 401，请在“API 配置”重新保存有效凭据；403 还应检查账号权限或套餐。
8. 分发软件时不要把管理员真实的 .auth 目录提供给无权限用户。

三、数据库
1. data\powertrade.db 是本地 SQLite 数据库。
2. 如果分发包内已经带有 data\powertrade.db，用户打开后可以直接浏览已有数据。
3. 如果没有预置数据库，软件会自动创建空数据库，并可继续采集新数据。
4. 最终分发包预置轻量演示数据库：ENTSO-E 603 条、Elexon 301 条、GridStatus 1000 条、Elecheck 1460 条、广州公开信息 241 条，另有 GridStatus 数据集目录 535 条。
5. 演示库仅在 data\powertrade.db 不存在时复制，不会覆盖用户已有数据库，也不包含凭据、会话、审批或定时任务。

四、注意事项
1. 请保持 PowertradeCrawler.exe 和 _internal 在同一个文件夹内；.env、configs、data 会在首次启动时自动创建。
2. 软件目录需要可写权限，否则无法创建数据库、缓存和导出文件。
3. 导出 CSV 时，如果目标文件正被 Excel/WPS 打开，可能会提示无权限写入。
4. 定时任务只有显示“Windows 触发器 = 已安装”后，才能在软件关闭时按计划自动运行。
5. 同一个定时任务并发触发时，后一次会被安全跳过；详细状态请查看任务运行记录。
6. 分发包内置 GridStatus 数据集目录快照；首次启动即使没有 API Key 或网络，也能浏览数据集信息，联网刷新后会更新目录。
