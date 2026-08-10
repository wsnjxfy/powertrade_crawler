# 新用户 API Key 与账号配置指南

Powertrade Crawler 可以在没有任何 API Key 的情况下启动，并浏览、筛选和导出已经保存到本地的数据。只有使用某个数据源的在线采集，或使用两套 Agent 时，才需要配置对应凭据。

图形界面入口：

```text
powertrade gui → 左侧“API 配置”
```

该页面会显示实时状态、官方入口和逐步配置说明。已保存的凭据只显示“已配置 / 待配置”，不会回显原值。

## 一、电力数据源

| 数据源 | 新用户要求 | 需要的凭据 | 说明 |
|---|---|---|---|
| GridStatus 北美 | 使用在线下载时必需 | GridStatus API Key | 本地数据浏览不需要；部分数据集受账号套餐限制 |
| ENTSO-E 欧洲 | 使用 Web API 采集时必需 | Security Token | 需要注册、邮件申请 RESTful API access，再在 My Account 生成 Token |
| Elecheck 易能电易查 | 使用在线采集时必需 | Authorization | 当前没有公开开发者 Key 自助页，需要数据服务方或项目管理员提供 |
| Elexon 英国 | 无需配置 | 无 | 当前 Insights API 公开访问；项目里的兼容槽位不是新用户必填项 |
| 广州电力交易中心 | 无需配置 | 无 | 读取公开市场信息与新闻 |

### 1. GridStatus

1. 打开 [GridStatus API 产品页](https://www.gridstatus.io/products/api)，注册或登录账号。
2. 进入 [GridStatus API Settings](https://www.gridstatus.io/settings/api)，创建或复制 API Key。
3. 在软件“API 配置 → 电力网站凭据 → GridStatus”中粘贴并安全保存。
4. 回到 GridStatus 页面，从较小的时间范围开始验证。

### 2. ENTSO-E Transparency Platform

1. 在 [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) 注册并完成邮件验证。
2. 按[官方 Security Token 指南](https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188-How-to-get-security-token)，向 `transparency@entsoe.eu` 发送主题为 `RESTful API access` 的申请邮件，并在正文填写注册邮箱。
3. 等待开通确认。官方说明通常在 3 个工作日内处理。
4. 登录平台，在 `My Account` 中生成 Security Token。
5. 在软件中粘贴并保存。不要误填 File Library 使用的短期 Bearer Token。

### 3. Elecheck 易能电易查

1. 在微信中搜索并登录“易能电易查”小程序，确认账号有权使用相关数据。
2. 当前没有公开的开发者 API Key 注册或自助生成页面。请向数据服务方或项目管理员申请有效 Authorization。
3. 在软件中粘贴对方提供的完整 Authorization 并保存。
4. 如果采集返回 HTTP 401，说明授权已失效，需要重新向授权方获取。

不要使用他人账号、绕过登录，或把 Authorization 放进聊天、截图、日志和 Git。

### 4. Elexon 与广州电力交易中心

- [Elexon Insights API 文档](https://bmrs.elexon.co.uk/api-documentation/introduction)说明当前接口可以直接访问，新用户不需要填写 API Key。
- [广州电力交易中心](http://www.gzpec.cn/)模块读取公开页面，不需要开发者账号或 API Key。

## 二、LLM 平台与本地免费模型网关

两套 Agent 不直接读取任何上游 LLM 平台 Key。它们只连接本机 `llm-router`：

```text
http://127.0.0.1:8317/
```

Agent 的配置要求分为两部分：

1. **Agent 功能必需**：本机已安装并启动 `llm-router`，且存在项目外的 `%USERPROFILE%\.config\llm-router\client-free.env`。
2. **单个平台均可选**：在下列平台中至少有一个免费渠道可用即可，不需要把每个平台都注册一遍。

| 平台 | 凭据 | 官方密钥入口 |
|---|---|---|
| Google Gemini | Gemini API Key | [Google AI Studio](https://aistudio.google.com/apikey) |
| ModelScope 魔搭 | SDK Token | [Access Token](https://modelscope.cn/my/myaccesstoken) |
| NVIDIA NIM | NVIDIA API Key | [NVIDIA API Keys](https://build.nvidia.com/settings/api-keys) |
| GroqCloud | Groq API Key | [Groq API Keys](https://console.groq.com/keys) |
| Cloudflare Workers AI | API Token + Account ID | [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens) |
| 智谱 AI | API Key | [智谱 API Keys](https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys) |
| GitHub Models | PAT（`models` 权限） | [GitHub Tokens](https://github.com/settings/tokens) |
| Mistral | Mistral API Key | [Mistral API Keys](https://console.mistral.ai/api-keys/) |
| OpenRouter | OpenRouter API Key | [OpenRouter Keys](https://openrouter.ai/settings/keys) |
| 硅基流动 | SiliconFlow API Key | [硅基流动 API 密钥](https://cloud.siliconflow.cn/account/ak) |

推荐流程：

1. 先在一个平台申请可用的免费 Key。
2. 打开本机网关管理页，添加或替换对应渠道的 Key 并测试。
3. 回到软件“API 配置 → LLM / Agent 配置”，刷新状态。
4. 看到至少一个“已接入 · 可用”渠道后，保持 `smart-auto` 即可。

项目中的 `siliconflow_api_key` 是旧版直连兼容槽位，当前两套 Agent 不读取它。新用户应把硅基流动等所有上游 Key 配置在本机网关中，而不是项目凭据文件中。

## 三、凭据保存与安全边界

- 电力数据源凭据保存在项目本地 `.auth/credentials.json`，整个 `.auth/` 目录已被 Git 忽略。
- 上游 LLM 平台 Key 只保存在项目外的本机网关配置中。
- Key 不进入 SQLite、提示词、任务参数、日志、评测报告或 Git。
- 页面不显示已经保存的真实值；更换 Key 时直接输入新值覆盖即可。
- `smart-auto` 只选择免费渠道；免费池全部不可用时会明确失败，不自动回退付费模型。
- 如果凭据疑似泄漏，应立即到对应平台官网撤销并重新生成。
