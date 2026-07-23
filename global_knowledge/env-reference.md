# `.env` 环境变量参数说明

项目根目录 `.env` 保存本机启动参数和密钥兜底。无密钥模板是 `.env.example`。框架使用内置解析器读取简单 `KEY=VALUE`，支持空行、`#` 注释、可选 `export ` 前缀以及单/双引号包裹的值。

## 加载与优先级

- 已存在于进程环境中的变量默认优先，`.env` 不覆盖它。
- Provider 密钥优先级：`user_config.json → provider.api_key` > `provider.api_key_env` 指向的环境变量 > 类型默认环境变量。
- Provider 模型优先级：`provider.model` > `KEMO_MODEL`/`OPENAI_MODEL`。
- Provider 地址优先级：`provider.base_url` > 环境变量 > 内置默认地址。
- Web 地址优先级：启动命令显式参数 > `WEB_HOST`/`WEB_PORT` > 内置默认值。

`.env` 只影响当前进程及其子进程。修改后，已启动的 RuntimeHost 通常需要重启才能完整生效。

## Provider

| 变量 | 适用模式 | 默认/说明 |
|------|----------|-----------|
| `KEMO_API_KEY` | `provider.type=kemo` | Kemo 网关密钥兜底 |
| `KEMO_BASE_URL` | kemo | 默认 `http://127.0.0.1:8741`，填写协议根地址，不要求 `/v1` |
| `KEMO_MODEL` | kemo | 仅当用户配置 `provider.model` 为空时使用 |
| `OPENAI_API_KEY` | `provider.type=chat` | Chat Completions 兼容密钥兜底 |
| `OPENAI_BASE_URL` | chat | 默认 `https://api.openai.com/v1`；缺少尾部 `/v1` 时框架自动补全 |
| `OPENAI_MODEL` | chat | 仅当用户配置 `provider.model` 为空时使用 |

`provider.api_key_env` 可以指定任意自定义环境变量名，例如 `MY_TEAM_API_KEY`。该自定义变量无需写入 `.env.example`，但应在部署说明中记录变量名，不能记录真实值。

## 网络代理

| 变量 | 说明 |
|------|------|
| `HTTP_PROXY` | HTTP 请求代理地址；留空直连 |
| `HTTPS_PROXY` | HTTPS 请求代理地址；留空直连 |

Provider 和使用标准网络栈的模块可继承代理。TLS 证书验证使用系统安全策略，不支持通过环境变量关闭验证。

## 工具插件

| 变量 | 说明 |
|------|------|
| `TAVILY_API_KEY` | `web_search` 使用的 Tavily 密钥；为空时运行时不向 Provider 暴露该工具 |

其他热插拔模块可以定义自己的环境变量，但必须使用清晰、避免冲突的前缀，并在模块文档中说明。平台 Token 不应写入 `message.json`、知识库、技能或日志。

## Web 服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_HOST` | `127.0.0.1` | 监听地址；`0.0.0.0` 会暴露到可达网络 |
| `WEB_PORT` | `1357` | 1–65535；端口冲突时启动器可继续探测后续端口 |
| `WEB_ACCESS_TOKEN` | 空 | 非空时启用 URL Token 登录，使用 `?token=...` 建立会话 |
| `WEB_USERNAME` | 空 | Web 页面登录用户名，与内部 `users/<name>` 无关 |
| `WEB_PASSWORD` | 空 | Web 页面登录密码，必须与 `WEB_USERNAME` 同时设置或同时留空 |
| `WEB_SESSION_SECRET` | 启动时随机生成 | 签名会话密钥；多进程或需要重启后保持登录时应显式设置强随机值 |
| `WEB_SESSION_COOKIE_NAME` | `kemo_agent_session` | Session Cookie 名称；同域多实例应使用不同名称 |

同时配置 Token 和用户名密码时，两种方式都可以建立已认证会话。业务接口使用 HttpOnly 签名 Cookie，不把密钥返回前端。

## 示例

```dotenv
KEMO_API_KEY=replace-with-local-secret
KEMO_BASE_URL=http://127.0.0.1:8741
KEMO_MODEL=my-model

WEB_HOST=127.0.0.1
WEB_PORT=1357
WEB_USERNAME=
WEB_PASSWORD=
WEB_SESSION_SECRET=
WEB_SESSION_COOKIE_NAME=kemo_agent_session

HTTP_PROXY=
HTTPS_PROXY=
TAVILY_API_KEY=
```

## 安全规则

1. `.env` 不提交版本库；只提交不含真实值的 `.env.example`。
2. 不把 `.env` 内容复制到知识库、记忆、截图、日志或错误报告。
3. 怀疑泄露时立即轮换密钥，不能只删除文件记录。
4. 对外监听 Web 时必须同时考虑认证、防火墙、反向代理和 HTTPS。
5. 自动化部署优先使用操作系统或平台的 Secret 管理，而不是把生产密钥写入镜像。

