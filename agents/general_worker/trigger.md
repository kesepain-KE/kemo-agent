# 注册信息

- **名称**: general_worker
- **触发**: 主智能体需要委派目标明确、可独立执行的执行型或重复性工作时
- **职责**: 文件处理、脚本、检索整理、批量操作、测试验证等通用工作
- **模型**: default（沿用现有子智能体模型配置）
- **工具**: 当前用户已授权插件，排除子智能体调度；技能、知识按当前用户授权使用
- **边界**: 拓展与感知不自动注入但可按需使用；禁止继续委派；不自动继承主历史

# 操作信息

## 调用方式

使用 subagent_dispatch，action=call、agent=general_worker。
input.task 必填且非空，明确任务目标；context 可选，传入必要事实摘要；
constraints、acceptance_criteria 为可选字符串数组。
不传工具授权、知识权限或是否允许委派等字段，权限由可信运行时决定。

```json
{
  "action": "call",
  "agent": "general_worker",
  "wait": false,
  "input": {
    "task": "检查指定目录内的 Markdown 链接并汇总失效项，不修改文件。",
    "context": "目标目录由当前任务提供，不扫描其他目录。",
    "constraints": ["只读检查，不访问外部服务"],
    "acceptance_criteria": ["报告扫描数量、失效链接及验证方法"]
  }
}
```

wait=true 等待结果；多个互不依赖的工作优先以 wait=false 分别提交，在共享 API 请求上限内
并行执行，可在网页右侧任务标签按稳定提交编号查看执行详情。同批结果按该编号交付；
后台提交仍是主任务必要依赖，不解除完成屏障。
后台任务仍为主任务必要依赖，queued 不等于完成，框架沿用完成屏障与结果自动续接。
不需要反复 status 轮询；取消沿用已有子任务取消机制。

返回对象含 status、summary、results、verification、remaining、artifacts。
读取具体 status 与遗留事项，不把外层任务执行成功等同于业务 completed。
采用独立上下文，主智能体应显式提供授权范围、必要背景与验收条件。
单次工具上限 80（仍受全局更小上限约束），整体超时沿用现有配置。
