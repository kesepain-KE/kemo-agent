# 注册信息

- **名称**: my_agent
- **触发**: 描述触发条件
- **职责**: 描述核心职责
- **模型**: cheap
- **工具**: 列出所需的插件和技能

# 操作信息

## 调用方式

由主智能体通过 `subagent_dispatch` 调用，`allowed_callers: ["main_agent"]`。

## 调用场景

### 场景一：默认场景

```
输入: { trigger: "default", data: {...} }

流程:
  1. 解析输入
  2. 执行核心逻辑
  3. 返回结果
```

## 输出格式

```json
{
  "result": "结果"
}
```

## 注意事项

- 所有阈值从 `config/global_config.json` 读取
- 不直接修改外部文件，只返回决策
