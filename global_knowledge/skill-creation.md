# 技能创建文档

技能（Skill）是注入智能体 Prompt 的 Markdown 指令，用来提供工作方法、领域规范和可复用流程。技能不会注册 Provider function call；真正可执行的工具只能放在 `plugins/`。

## 作用域

| scope | 路径 | 使用者 |
|-------|------|--------|
| `agent_create` | `users/<user>/user_skills/agent_create/<name>/` | 主智能体与 `self_improve` |
| `user_create` | `users/<user>/user_skills/user_create/<name>/` | 当前用户主智能体 |
| `shared` | `shared_skills/<name>/` | 所有允许该共享技能的用户主智能体 |

共享技能受 `skills.shared_whitelist` 过滤；空数组表示全部允许。用户技能按当前用户目录发现。目录可以嵌套，白名单名称使用相对路径，例如 `development/python`。

## 标准结构

```text
<skill-root>/<name>/
├── SKILL.md          # 唯一必需文件
├── references/       # 可选参考资料，不会自动全部注入
├── scripts/          # 可选辅助脚本；技能本身不会把它注册成工具
└── assets/           # 可选模板、图片等静态资源
```

运行时递归寻找 `SKILL.md`。文件必须包含一级标题；一级标题之后、下一个二级标题或 `---` 之前的内容作为技能描述。

## 推荐格式

```markdown
# release_check

在发布前检查版本、测试、变更记录和构建产物。

## 适用场景

- 用户要求准备或审查发布。

## 执行流程

1. 确认目标版本和发布范围。
2. 运行项目规定的测试与构建。
3. 汇总阻塞项，不伪造通过结果。

## 禁止事项

- 未经确认不发布、不推送、不删除。

## 参考

- `references/checklist.md` — 详细检查表
```

可以增加 `## Tool` JSON 作为参数文档，但它仅用于说明，不会成为可调用工具。

## 选择技能还是其他能力

| 需求 | 应选能力 |
|------|----------|
| 可复用说明、规范、工作流 | 技能 |
| 真实文件、网络或系统操作 | `plugins/` 工具 |
| 外部服务状态与操控 | 拓展 |
| 独立 LLM Prompt、权限和工具循环 | 子智能体 |
| 一次性任务 | 直接执行，不创建长期能力 |

## 创建与更新流程

1. 确认需求具有稳定复用价值。
2. 确认 `agent_create`、`user_create` 或 `shared` 作用域。
3. 确认目录名、标题、描述、适用/不适用场景和正文。
4. 使用 `skill_creater action=list` 查重。
5. 用户确认后执行 `create`；修改已有技能使用 `get` 后再 `update`。
6. 执行 `validate`，确认主智能体的技能诊断能发现它。

`skill_creater` 支持完整 `content` 写入，或 `title + description + instruction/tool_schema` 结构化写入。`instruction` 与 `tool_schema` 二选一。

## 编写规则

- 一个技能只解决一个清晰主题，标题和目录名保持稳定。
- 指令必须可操作，写清触发条件、步骤、失败处理和禁止事项。
- 引用附加资源时使用相对路径；未被显式读取的参考文件不会自动生效。
- 不在技能中存储密钥、Token、密码、Cookie 或个人隐私。
- 不把“文档化 Tool”描述成已经可执行的工具。
- 删除或扩大共享技能影响范围前必须确认。

