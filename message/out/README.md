# 外部消息插件目录

每个直接子目录代表一个平台适配器。RuntimeHost 启动时读取该目录中的
`message.json`，加载其中明确声明的 `input.py`、`output.py` 和 `detect.py`；
同目录下其他 Python 文件不会被核心自动执行。

## 文件契约

- `message.json`：静态配置及绑定用户、能力和工具权限。
- `state.json`：健康状态与收发计数，由运行时和检测模块更新。
- `message.md`：YAML front matter + Markdown 正文组成的文件队列。
- `files/`：入站附件，仅允许 `message.md` 引用本目录内的文件。
- `log/YYYY-MM-DD.md`：每条已处理消息的入站、附件和出站记录。

详细字段及示例见 `global_knowledge/外部消息模块插件化-编程规划.md` 和
`message/out/example/`。
