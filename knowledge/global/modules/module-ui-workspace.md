# 拓展与感知的网页工作台

感知和拓展复用现有模块注册、中间层、白名单、隔离执行与配置机制。工作台左栏选择作用域，
中栏选择具体模块，右栏显示模块详情、横向功能入口和实际工作区。Expand 继续使用 global、
shared、user；Sense 当前只有 global，不新增用户感知模型。

模块可以同时提供 `module/panel.json` 通用组件和任意 HTML/CSS/JavaScript 网页。
网页可以自行选择前端框架、布局、图表与内部页面；宿主负责静态资源、隔离、授权和通信。
中栏模块卡片只显示名称和健康指示，第二行左侧显示作用域与模块目录名，右侧显示启用状态和
注入 Tokens；说明保留在详情中。搜索仅匹配显示名称与模块目录名，不匹配模块说明。

## 声明与目录

保留原 `module.yaml` 字段，增加：

```yaml
ui:
  workspace: module/workspace.json
  # 仅 Sense 的自定义网页动作需要；不会开放主智能体操控工具。
  action_entry: ui_actions.py
```

Expand 网页动作复用 `control_script`，不声明 `action_entry`。UI 文件支持模块内多级相对路径；
采集、Prompt 数据与操控入口仍遵循原来直接子级文件合同。任何声明路径均不得越界或经过链接。

```text
模块目录/
  module.yaml
  module/panel.json
  module/panel.values.json
  module/workspace.json
  ui/index.html
  ui/src/...
  ui/dist/index.html
  ui/dist/assets/...
```

最小 `module/workspace.json`：

```json
{
  "schema_version": 1,
  "default_page": "console",
  "pages": [
    {
      "id": "console",
      "label": "专属控制台",
      "type": "web",
      "entry": "ui/dist/index.html",
      "permissions": ["module.status.read", "module.action.invoke"]
    },
    {"id": "panel", "label": "通用配置", "type": "panel"},
    {"id": "guide", "label": "使用指南", "type": "markdown", "source": "README.md"}
  ],
  "actions": [
    {
      "id": "refresh",
      "label": "立即采集",
      "handler": "refresh",
      "input_schema": {"type": "object", "properties": {}, "additionalProperties": false}
    }
  ]
}
```

页面支持 `web`、`panel`、`markdown`，以及 `builtin` 的 `overview`、`collection`、`injection`、`docs`、`logs`。
总览、采集数据、注入数据、通用配置、文档和运行记录按实际模块能力自动补充，不要求每个模块都有相同入口。
采集数据与注入数据使用两个独立的功能标签：`collection` 展示 `collected_markdown` 原文，
`injection` 展示遵循当前数据空间白名单与上下文预算的 `injected_markdown`。
当前没有注入内容时显示未注入状态，不用采集原文填充。
声明已有同名 ID 时使用声明项。无 `ui` 的模块继续正常显示通用面板与已有能力。
UI 声明错误只在该模块显示诊断，保留其原有运行能力与可用面板。

## 网页构建与 SDK

网页由 `sandbox="allow-scripts"` iframe 加载，使用不透明源。不得访问父页面 DOM、
Cookie 或宿主存储；不允许直接访问外部 API、嵌套网页、原生表单提交或任意模块文件。
读写和网络业务通过模块后端完成。静态资源仅发布 `ui/dist`，限制为 32 MiB、2048 文件。

HTML、脚本、样式、字体和动态 chunk 使用相对路径。内部路由使用 hash，避免绝对路径跳出
当前实例资源命名空间。构建时不要把宿主 SDK 打入产物：

```js
// 本仓库示例使用 Vite external，将此导入映射为 ../__kemo__/bridge.js。
// 映射相对于 assets/*.js；直接写在根 HTML 中则用 ./__kemo__/bridge.js。
import { kemo } from '/__kemo_bridge__'

const context = await kemo.ready
const status = await kemo.request('module.status.get')
await kemo.request('module.action.invoke', { action: 'refresh', params: {} })
kemo.onEvent((event, value) => {
  if (event === 'host.theme.changed') applyTheme(value)
})
```

`npm --prefix web/frontend run build:module-uis` 构建三个内置拓展的专属控制台：
`expand/global/kemo_app/ui`（桥接生命周期、在线设备、端口、Token 和用户绑定）、
`expand/global/kemo_gateway_status/ui`（网关快照、模型、调用记录与地址）、
`expand/global/kemo_graph/ui`（文档库、实时状态、检索与连接），以及 `template/sense-web/ui` 示例。
控制台通过声明的 Bridge 动作复用各自的现有后端入口，通用配置面板同时保留。
三个内置控制台提供本地初始化与待配置提示；App、网关使用 `initialize`，
Graph 使用 `initialize_config`，初始化保留已有配置，不自动启动服务或联网。
网关连接设置还可提交独立 `STATUS_TOKEN` 验证并激活，后端不向网页返回 Token。
完整宿主构建也运行此步骤。
自建模块自行构建 `ui/dist` 后随模块分发，部署机无需 Node.js。

在按钮的 `click` 事件中调用 SDK。使用表单排版时把操作按钮设为 `type="button"`，需要时调用
`reportValidity()`；沙箱禁止原生 submit，不要为提交配置增加 `allow-forms`。

## 通信与授权

现有 Web 认证是实例管理员认证；`user` 选择数据空间，不是独立认证租户。
网页首次请求能力时由管理员审查。全局/共享模块的授权按物理模块保存，用户模块按用户隔离，
授权文件为实例本地 `config/module_ui_grants.json`，不随源码提交或安装包分发。

| Bridge 方法 | 所需权限 | 行为 |
|---|---|---|
| `module.status.get` | `module.status.read` | 当前模块状态、脱敏 panel 状态、采集及注入文本、运行诊断 |
| `module.config.get` | `module.config.read` | 读取通用配置及 `config_revision`，密钥只返回是否设置 |
| `module.config.patch` | `module.config.write` | 提交 `values`、`clear_secrets`、原 `config_revision`，保存后采集 |
| `module.action.invoke` | `module.action.invoke` | 仅调用声明的 action ID 和符合 schema 的参数 |
| `module.events.subscribe` | `module.status.read` | 订阅 `module.status.changed`，可见页面约每 5 秒刷新 |
| `host.theme.get` / `host.locale.get` | 无 | 获取宿主主题变量、语言 |
| `host.resize.request` | 无 | 放大当前工作区 |
| `host.dirty.set` | 无 | 声明未保存状态；通常使用 `kemo.setDirty(boolean)` |

`kemo.ready` 返回模块身份、数据空间、已授予权限、主题和语言。使用 `kemo.onEvent` 处理
`host.theme.changed`、`host.locale.changed`、`module.status.changed`、`module.error`。
宿主核对 iframe 来源、nonce、request_id 和权限；实例随机令牌有效期 24 小时，关闭或撤权立即撤销，
RPC 有并发、频率和消息大小限制。静态资源能力 URL 只可读取本实例公开资源，RPC 仍经过宿主认证。
不要把实例 URL 或令牌写入日志、分享链接或配置。

配置保存按模块执行锁比较原始版本；其他页面已经修改时返回冲突，不覆盖对方配置。
网页应保留草稿并让用户重新读取。SDK 返回错误 code 和 message，禁止把失败当作成功。
调用等待超时或页面关闭不代表后端副作用已取消；核对真实状态后再决定是否重试。

自定义动作的 `handler` 对应现有 `execute(command, params, context=None)`；Sense 从
`ui.action_entry` 加载，Expand 复用操控入口。`handler="refresh"` 直接调用原采集链。
输入使用 JSON Schema object，禁止未声明顶层字段及 `$ref`、`$dynamicRef`。
动作需返回 JSON 兼容结果，读取自建文件时由模块负责脱敏。网页授权限制浏览器能力，
后端插件仍以现有本机权限执行，并非操作系统安全沙箱。

## 更新、启停与工作区状态

工作台每 5 秒重新读取真实库存。模块增删、UI 声明或资源变化无需重启；
健康、采集时间和配置值变化不会重建网页。
库存和网页实例校验不递归读取模块内部的文件清单；独立文件管理接口继续提供完整清单。
对话输入框的能力引用使用 `GET /api/users/{user}/expand?include_files=false`，只读取模块信息，
不扫描模块自带的运行时、依赖和其他内部文件；引用列表在前端保鲜 30 秒。
拓展、技能列表的磁盘读取在工作线程执行，不占用 Web 事件循环。
网页实例仅校验目标模块的 UI 声明与资源；库存扫描不持有实例管理锁，
静态资源加载不再等待其他模块的 UI 校验。停用、撤权和 UI 变更仍在每次请求时核验。
放大、恢复和原生全屏保持同一 iframe 与组件实例，保留内部导航和未保存输入。
切模块、功能或路由前，声明了 dirty 的页面会提示放弃修改；浏览器关闭使用原生离开提示。

UI 更新会使旧实例后续调用失效。无草稿时加载新版本；有草稿时保留当前页面供检查和复制，
提示用户放弃未保存内容后更新。此时保留的是旧页面状态，不能继续使用旧令牌保存。
权限声明变化要求重新审查，撤权、管理接口停用和卸载撤销已有实例。
已派发的后端操作按原执行合同结束，不通过页面重建自动重复执行。

## ZIP 安装与更新

从工作台“导入模块”选择作用域和 ZIP，先预览文件、后端入口和网页权限，再确认安装。
ZIP 可以直接包含模块文件，或包含一个模块根目录。拒绝绝对路径、路径穿越、链接、
特殊文件、隐藏文件、大小写冲突和凭据文件；限制压缩包 16 MiB、解压后 64 MiB、4096 文件。
预览 10 分钟有效，最多同时保留 4 个待确认包。

更新与采集/操控共用模块执行锁，保留 `.env`、现有输入文件、模块配置/状态和 SQLite 数据，
替换已审查源码和资源，最后发布清单；失败还原已替换文件。
原目录备份保存在实例 `tmp/module-backup-*`，不会自动删除。
不需要重新实现或绕过模块注册根目录；新装模块沿用已有白名单规则。

验收示例：复制 `template/sense-web` 到 `sense/<目录名>`，或打成 ZIP 导入，
在“时间控制台”授权后读取真实系统时间、保存时区并重新采集；
切到“通用配置”操作同一配置。网关示例读取已有真实安全快照，无快照时明确显示空态。
