# V16 Layered Observer UI 迁移状态

标准参考：`E:\下载\kemo-agent-ui-v16-layered-view.html`。

## 页面

- `/chat`：真实用户、会话、历史、POST SSE、RunEvent、上下文用量、活动计划与最近活动
- `/tasks`：独立任务中枢，读取 PlanStore / CronStore 安全摘要和真实空态
- `/knowledge`：独立文件知识页，读取用户层与全局层索引元数据
- `/skills`：独立技能中心，读取实际工具注册表和来源层级
- `/sense`：独立全局感知观察页；未实现注册运行时时不生成演示来源
- `/settings`：独立配置概览，提供外观设置与脱敏运行配置镜像

## AppShell

- V16 分层侧栏、最近会话、用户切换菜单和折叠态
- 真实健康状态、上下文 Token 用量与占用比例
- 当前 Provider / 模型只读菜单
- 字号与高级白/高级黑主题
- 运行状态抽屉：上下文、Provider、知识、工具、子代理和最近活动
- 可搜索命令面板，支持 `Ctrl+K` 与页面导航

## ChatPage

- 欢迎卡、真实运行指标、活动计划、快捷入口和最近活动
- 文本、思考与工具调用流式卡片
- 可用的知识、感知和技能页面入口；未接入的文件/图片按钮明确禁用
- 新会话可用；保存状态说明为自动提交；手动压缩明确标记未接入

## 数据原则

- 标准稿中的演示数字、传感器、外接项目状态和任务不进入正式页面。
- Observer API 只返回页面所需的安全摘要，不返回 API Key、环境变量值、知识正文、Cron prompt、工具参数/结果或绝对路径。
- 全局感知说明目录不等同于已注册来源；没有注册表时显示真实空态。
- Observer API 本身不启动 RuntimeHost；前端开发使用 `start_web.py --no-host`，避免第二个 CronScheduler。

## 样式组织

- `src/styles/prototype.css`：设计 token、Shell、聊天、分层模块、表格、卡片、设置和响应式布局。
- `src/styles/app.css`：React 专用模型菜单、活动任务、最近活动、命令面板和抽屉增强。
- 关键断点：`1250px` 顶栏收缩、`1100px` 模块重排、`780px` 窄屏侧栏、`520px` 单列卡片。

## 自动化覆盖

- 后端 Observer API 的真实数据读取与脱敏测试
- 前端 5 个模块页面的结构/真实空态测试
- AppShell 命令面板、Provider、上下文和主题控件测试
- Playwright 桌面、780px 窄屏与暗色主题视觉回归
