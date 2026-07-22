## 注入层

# 拓展模块名称

> 自动采集时间：{{采集时间}}

## 数据

{{数据内容}}

## 操作层

# 拓展操控工具

助手通过调用 `start_expand.py` 中的 Python 函数操控拓展模块。
所有命令通过 `execute(command, params)` 统一入口调度，返回 JSON 字符串。

## 可用命令

### example_action(param: str)
描述此操作的功能。
