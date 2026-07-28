# 拓展验收标准

```powershell
python -m tests.template_tests.expand --target users/<user>/expand/<name>
```

## 必须成立

- `expand.json` 可解析，所有声明入口和出口都留在模块边界内；
- 采集端提供同步零参数 `update()` 或 `main()`，成功时刷新清单声明的 Markdown
  Prompt 摘要出口和健康状态；
- Prompt 来源注册器能够发现采集数据和操控说明；
- 开启操控端时，薄入口兼容 `execute(command, params)` 或旧版
  `execute(command_dict)`，并通过 JSON 子进程协议返回结果或结构化拒绝。

## 数据自由度

采集端和操控端可以处理 JSON、Markdown、CSV、DOM、数据库、嵌入式状态、图片、音频、
视频或任意其他数据。`input_data.md` 只承载适合注入 Prompt 的采集摘要/资源引用；操控结果
不要求经过它，而是直接通过工具结果和 artifacts 出口返回。内部可以容纳完整开源项目。
