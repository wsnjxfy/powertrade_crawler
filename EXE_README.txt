Powertrade Crawler 使用说明

一、启动方式
1. 解压整个文件夹，不要直接在压缩包里运行。
2. 双击 PowertradeCrawler.exe。
3. 首次启动会自动创建 .env、data 目录和本地数据库结构。

二、鉴权文件
1. 所有 API key、token 和 Authorization 统一保存在 exe 同级目录的：
   .auth\credentials.json
2. 该文件包含 GridStatus、Elecheck 和 ENTSO-E 三种凭据。
3. .env 只保存数据库、超时和请求间隔等非敏感配置，不再保存凭据。
4. Elecheck AUTHORIZATION 不按固定时间自动失效。
5. 如果 Elecheck 采集时返回 401，软件会提示“授权已过期，请联系管理员更新授权文件”。
6. 分发软件时不要把管理员真实的 .auth 目录提供给无权限用户。

三、数据库
1. data\powertrade.db 是本地 SQLite 数据库。
2. 如果分发包内已经带有 data\powertrade.db，用户打开后可以直接浏览已有数据。
3. 如果没有预置数据库，软件会自动创建空数据库，并可继续采集新数据。

四、注意事项
1. 请保持 PowertradeCrawler.exe、_internal、.env.example、.auth、data 在同一个文件夹内。
2. 软件目录需要可写权限，否则无法创建数据库、缓存和导出文件。
3. 导出 CSV 时，如果目标文件正被 Excel/WPS 打开，可能会提示无权限写入。
