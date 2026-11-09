# Sense 独立网页示例

此模板读取真实系统时钟，展示时间观测并通过宿主 Bridge 修改时区、执行采集。
通用 `panel.json` 与独立网页同时保留，`control_open` 仍然为 false。

在仓库根运行 `npm --prefix web/frontend run build:module-uis`，将本目录复制到
`sense/<自选目录名>`，或将本目录内容打包为 ZIP 后从感知工作台导入。
进入时间控制台前由实例管理员审查权限；通用配置页无需插件网页授权。
完整声明、SDK 与安全合同见
`knowledge/global/modules/module-ui-workspace.md`。
