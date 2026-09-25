# Kemo Graph 用户配置面板

- 只开放知识图谱后端协议、IP/主机、端口和远程地址安全开关；库注册、来源目录和权限列表仍只读。
- 本地回环地址允许 HTTP；非回环地址必须显式开启 `allow_remote`，并继续遵守原合同的 HTTPS 限制。
- “检测知识图谱后端”调用当前配置的 `/api/v1/status`，把在线、离线、检查时间和安全错误摘要写入 `module/status.json`。
- 地址保存会保留现有库注册表，不修改 `expand.json` 或 Prompt 注入配置。
