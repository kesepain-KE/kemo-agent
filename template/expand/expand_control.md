## 注入层

# 拓展模块名称

模块职责说明。最新状态由数据采集入口写入 `input_data.md`，模型请求时由 Prompt 管线读取；操作层用于说明可调用能力。

## 操作层

# 拓展操控工具

助手通过调用 `start_expand.py` 中的 Python 函数操控拓展模块。
所有命令通过 `execute(command, params)` 统一入口调度，返回 JSON 字符串。

## 可用命令

### example_action(param: str)
描述此操作的功能。
