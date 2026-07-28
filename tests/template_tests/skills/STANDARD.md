# 技能验收标准

```powershell
python -m tests.template_tests.skills --target users/<user>/user_skills/<scope>
```

## 必须成立

- 候选目录内可以递归找到一个或多个大小写兼容的 `SKILL.md`；
- 每个技能有一级标题和用于发现的非空描述，实际模块不能残留模板占位符；
- 嵌套技能能够被真实 Prompt 来源注册器发现；
- Markdown 中明确引用但不存在的相对资源会给出警告。

## 不限制

技能不是固定章节表单。除标题和发现描述外，可以自由组织指令、脚本、参考资料、素材、
子目录或完整工程；资源不会被自动全部注入，智能体根据 `SKILL.md` 按需读取和执行。
