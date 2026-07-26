# sense_creater

热创建、列出并校验全局感知模块。感知模块只采集系统或环境数据，并通过 `sense.md` 单向注入 system prompt，不提供外部操控接口。

## 创建感知模块四步流程（必须遵守）

收到创建感知模块的请求后，不得立即调用 `action=create`。必须完成以下四步，并在关键设计未经用户确认时暂停创建。

### 第一步：判断是否真的需要感知模块

适合感知模块：

- 采集 CPU、内存、磁盘、温度、天气、网络状态或设备在线状态等数据。
- 数据只按“采集 → 注入 Prompt”单向流动，不需要智能体操控外部对象。
- 数据需要定期刷新，可后续配合 cron 执行 `data_update.py`。

不适合感知模块：

- 需要操控外部设备、API 或服务时，使用 `expand_creater`。
- 需要可复用指令时，使用 `skill_creater`。
- 需要独立 LLM 推理和工具调用循环时，使用子智能体。
- 只是一次性查询或脚本时，直接完成任务。

### 第二步：确认基本信息

向用户确认英文模块目录名、一句话功能说明以及需要采集的数据。名称必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。

### 第三步：确认数据出口

帮助用户确认哪些数据允许通过 `sense.md` 进入 Prompt，并准备一个有界的初始数据出口。不得把凭据或不应进入 system prompt 的敏感数据写入模板。不要替用户预设内部项目结构；从单文件到完整工程都合法。

### 第四步：检查冲突与创建

1. 调用 `action=list`，确认 `global_sense/` 下没有同名模块。
2. 汇总名称、功能说明、数据出口和框架入口合同，不强制内部实现布局。
3. 获得用户最终确认后调用 `action=create`，不得覆盖或偷偷改名。
4. 创建成功后报告模块路径、创建器建立的最小合同文件和校验结果；若需求尚未实现，继续使用正常文件或代码工具在该模块目录内自由完善，而不是把所有代码塞进入口文件。

## 模块目录不是固定模板

`sense_creater` 只负责原子建立框架能够发现的最小合同。新模块目录内部是自由工作区，可以继续创建任意文件和任意层级目录，也可以迁入完整工程；模板未列出的文件不会因此非法。`data_update.py` 可以是极小实现，也可以只是调用内部工程的适配入口。不要输出或套用固定的推荐目录树，按需求本身决定规模和组织方式。

## 与 expand_creater 的区别

| | `sense_creater` | `expand_creater` |
|---|---|---|
| 目标目录 | `global_sense/` | `shared_expand/` 或用户 Expand |
| 数据流 | 单向采集并注入 | 数据注入与外部操控 |
| 创建器建立的最小合同 | 清单、数据出口和采集入口 | 清单、数据/说明出口和采集/操控入口 |
| scope | 只有全局层，无需参数 | `user` / `shared` |

## 安全与运行时约束

- 创建采用临时目录发布，发布后立即校验；失败会删除整个新模块。
- 模块目录及清单引用文件不得是符号链接或目录联接，也不得跳出 `global_sense/`。
- `sense.json` 严格包含五个字段：`name`、`data_md`、`recent_update`、`health`、`start_update`。
- 自定义 Python 在写入前检查语法和疑似硬编码凭据；认证信息必须从环境变量读取。
- `sense.md` 全文可能进入 system prompt，只应保存允许所有启用该模块的用户看到的数据。

## 参数说明

| 参数 | 适用 action | 说明 |
|---|---|---|
| `action` | 全部 | `list` / `create` / `validate` |
| `name` | `create` / `validate` | 英文模块目录名 |
| `explain` | `create` | 一句话功能说明，仅用于创建确认和返回结果，不写入五字段清单 |
| `sense_content` | `create` | 初始 `sense.md` Markdown 数据模板 |
| `data_update` | `create` | 可选自定义 `data_update.py`；不传则使用安全骨架 |

## Tool

```json
{
  "name": "sense_creater",
  "description": "热创建、列出或校验全局感知模块；创建前必须完成必要性判断、名称与采集目标确认、数据模板确认和冲突检查四步流程。感知是采集到 Prompt 注入的单向数据流，不提供操控接口。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "create", "validate"],
        "description": "list=列出模块，create=创建模块，validate=校验模块"
      },
      "name": {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
        "description": "create/validate 使用的模块目录名"
      },
      "explain": {
        "type": "string",
        "maxLength": 2000,
        "description": "create 使用的一句话功能说明，不写入 sense.json"
      },
      "sense_content": {
        "type": "string",
        "maxLength": 100000,
        "description": "create 使用的初始 sense.md Markdown 数据模板"
      },
      "data_update": {
        "type": "string",
        "maxLength": 500000,
        "description": "create 可选自定义 data_update.py；不传则使用安全骨架"
      }
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
