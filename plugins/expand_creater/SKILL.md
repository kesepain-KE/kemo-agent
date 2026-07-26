# expand_creater

热创建、列出并校验当前用户拓展或共享拓展。工具只建立框架可发现的最小合同文件；模块内部实现和工程结构不受模板限制，创建后立即使用真实 Expand 运行时契约复验。

## 创建拓展模块四步流程（必须遵守）

收到创建拓展模块的请求后，不得立即调用 `action=create`。必须依次完成以下四步；只要关键设计仍未经用户确认，就暂停创建并继续沟通。

### 第一步：批判性判断是否需要拓展模块

适合创建拓展模块：

- 需要对接外部设备、API 或服务，例如智能家居、摄像头、传感器和业务平台。
- 需要定期采集外部数据并注入主智能体 system prompt。
- 需要主智能体通过 `expand_call` 隔离调用 `start_expand.py` 操控外部能力。

不适合创建拓展模块：

- 纯内部 LLM 推理或独立提示词任务，应优先考虑子智能体。
- 单次查询、一次性文件处理或普通脚本执行。
- 只需要提供操作说明而不连接外部能力，应优先考虑技能。

如果不需要拓展模块，应说明原因并建议子智能体、技能或直接脚本等更合适的方案。

### 第二步：确认 scope 和基本信息

向用户确认：

1. 放在 `user`（`users/<user>/expand/`）还是 `shared`（`shared_expand/`）。
2. 英文目录名，必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。
3. 一句话功能说明，写入 `expand.json` 的 `explain`。

不得创建 `global` 拓展；全局层属于管理员维护范围。

### 第三步：确认注入层和操作层

`expand_control.md` 是拓展的核心文档：

- 注入层：说明拓展是什么、能做什么、何时使用以及如何找到操作入口；这部分可进入主智能体 system prompt。
- 操作层：完整说明 `expand_call` 使用的 scope、模块名、指令名、参数、返回值、失败格式、文件产物和必要限制；这部分按需读取，不自动注入。

采集侧的 `input_data.md` 只保存适合进入 Prompt 的摘要或资源索引；完整 JSON、CSV、HTML、图片、音视频和大型日志保存在模块内由实现自行选择的位置。操控结果直接通过工具返回，不能绕写 `input_data.md`。

这些文件名和目录名只说明框架输入输出边界，不规定拓展内部结构。不要先给出固定工程样例再要求实现套用；拓展既可以是极小单文件能力，也可以容纳完整开源项目，并由清单声明的入口做薄适配。

引导用户描述真实操作接口，并整理为结构化 Markdown。传给 `injection`、`operations` 的正文不要重复包含 `## 注入层` 或 `## 操作层` 标题，工具会统一生成标题。

### 第四步：检查冲突与创建

1. 调用相同 scope 的 `action=list`，确认没有同名模块。
2. 检查名称、说明、注入层、操作层以及是否开启数据注入。
3. 向用户汇总并最终确认上述内容。
4. 用户明确确认后调用 `action=create`；不得偷偷改名、覆盖现有模块或放宽范围。
5. 创建成功后报告路径、创建器建立的最小合同文件和运行时校验结果。随后可使用正常文件或代码工具在模块目录内自由创建、复制或迁入实现，不得把所有功能强行塞进两个入口脚本。

## 模块目录不是固定模板

`expand_creater` 建立的是框架最小合同，不是完整工程脚手架。拓展目录内部可以包含任意文件、任意层级目录、已有项目源码、资源、配置和构建产物；未知内部文件不会自动注册、注入或执行，也不应仅因模板没有列出而被拒绝。保持原项目结构或采用极小实现都可以，唯一要求是清单声明的入口最终适配框架协议并留在模块作用域内。

## 已有拓展操作

- `list`：列出一个 scope 的模块、功能说明、输入健康状态和结构校验结果。
- `validate`：校验指定模块的清单字段、引用文件、Python 语法、操控文档结构、路径边界与真实运行时兼容性。

## 安全约束

- `create` 不覆盖同名目录，目录级临时写入完成后才发布；创建后校验失败会回滚整个新模块。
- 模块目录和清单引用文件不得是符号链接或目录联接，也不得跳出目标 scope。
- 说明、Markdown 和自定义 Python 中不得写入疑似 API Key、Token、密码或其他敏感凭据；代码应从环境变量读取密钥。
- 根目录 `template/expand/` 是创建器的唯一默认模板来源，创建器不得维护另一套不一致的调用协议。
- 默认 `start_expand.py` 返回明确的未实现错误，不会伪造外部设备已经执行成功。

## 参数说明

| 参数 | 适用 action | 默认值 | 说明 |
|------|-------------|--------|------|
| `action` | 全部 | 必填 | `list` / `create` / `validate` |
| `scope` | 全部 | 必填 | `user` / `shared` |
| `name` | create / validate | — | 英文模块目录名 |
| `explain` | create | — | 一句话功能说明 |
| `injection` | create | — | 注入层正文 |
| `operations` | create | — | 操作层正文 |
| `open_input` | create | true | 是否允许健康数据进入 Prompt |
| `start_expand` | create | 模板 | 可选自定义操控入口 Python 源码 |
| `data_update` | create | 模板 | 可选自定义采集入口 Python 源码 |

## Tool

```json
{
  "name": "expand_creater",
  "description": "列出、创建或校验用户拓展与共享拓展；创建前必须完成必要性判断、scope/名称确认、注入层/操作层确认和冲突检查四步流程。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "create", "validate"],
        "description": "list=列出模块，create=创建模块，validate=校验模块"
      },
      "scope": {
        "type": "string",
        "enum": ["user", "shared"],
        "description": "user=当前用户拓展，shared=共享拓展"
      },
      "name": {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
        "description": "create/validate 使用的模块目录名"
      },
      "explain": {
        "type": "string",
        "maxLength": 2000,
        "description": "create 使用的一句话功能说明"
      },
      "injection": {
        "type": "string",
        "maxLength": 100000,
        "description": "create 使用的注入层正文"
      },
      "operations": {
        "type": "string",
        "maxLength": 100000,
        "description": "create 使用的操作层正文"
      },
      "open_input": {
        "type": "boolean",
        "default": true,
        "description": "是否开启健康数据注入"
      },
      "start_expand": {
        "type": "string",
        "maxLength": 500000,
        "description": "可选自定义 start_expand.py；不传则使用安全模板"
      },
      "data_update": {
        "type": "string",
        "maxLength": 500000,
        "description": "可选自定义 data_update.py；不传则使用安全模板"
      }
    },
    "required": ["action", "scope"],
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
