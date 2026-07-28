# 子代理验收标准

```powershell
python -m tests.template_tests.agent --target users/<user>/agents/<name>
```

## 必须成立

- 根目录清单能被 `discover_agents()` 发现，名称、指令、触发注册和能力配置有效；
- `AGENT.md` 与 `trigger.md` 非空，公开/内部调用权限与发现结果一致；
- 输入和输出是 object JSON Schema，声明的插件工具在当前框架存在；
- 使用自定义执行器时，清单同目录的薄入口提供同步
  `execute(context, input_data) -> AgentRunResult`；
- 动态验收使用假 Provider 和 Schema 样例完成输入到输出闭环，不消耗真实模型额度。

## 不限制

执行器内部可以导入任意嵌套模块、完整工程或第三方项目。额外文件不会自动进入 Prompt，
由 `AGENT.md` 或可信薄入口按需使用。验收器不规定类名、内部路由、文件数量或目录层级。
