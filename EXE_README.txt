Powertrade Crawler 使用说明

一、启动方式
1. 解压整个文件夹，不要直接在压缩包里运行。
2. 双击 PowertradeCrawler.exe。
3. 首次启动会自动创建 .env、data 目录和本地数据库结构。

二、授权文件
1. 小范围分发时，管理员负责维护同目录下的 .env 文件。
2. 如需采集 Elecheck 数据，请在 .env 中填写：
   ELECHECK_AUTHORIZATION=管理员提供的授权
3. AUTHORIZATION 不再按固定时间自动失效。
4. 如果采集时返回 401，软件会提示“授权已过期，请联系管理员更新授权文件”。

三、数据库
1. data\powertrade.db 是本地 SQLite 数据库。
2. 如果分发包内已经带有 data\powertrade.db，用户打开后可以直接浏览已有数据。
3. 如果没有预置数据库，软件会自动创建空数据库，并可继续采集新数据。

四、注意事项
1. 请保持 PowertradeCrawler.exe、_internal、.env.example、data 在同一个文件夹内。
2. 软件目录需要可写权限，否则无法创建数据库、缓存和导出文件。
3. 导出 CSV 时，如果目标文件正被 Excel/WPS 打开，可能会提示无权限写入。
