# 网页正文内联组件

本文定义 Web 对话中 `kemo-widget` 的生成、解析、渲染、持久化与安全边界。它是正文的一部分，
不是工具调用、媒体产物或独立 iframe 应用。组件用于把适合结构化表达的数据直接镶嵌在智能体正文段落之间；
没有明显理解收益时继续使用普通 Markdown。

全局人格负责让智能体稳定知道“自己具备这种原生表达能力”，并形成主动但克制的可视化倾向；
`agents.md` 负责高频使用判断和硬约束；本文是协议字段、组件目录和渲染边界的权威正文。三者不得把
内联组件描述成额外事实来源、工具权限或后端执行能力。

## 输出载体

智能体在 Web 回复正文中直接输出闭合的 fenced block：

````markdown
正文说明。

```kemo-widget
{
  "protocol": "kemo-ui",
  "schema_version": "1.0",
  "surface": "inline",
  "id": "physics-1-comparison",
  "component": "comparison-card",
  "props": {
    "title": "大学物理（1）",
    "status": {"label": "补考通过", "tone": "success"},
    "items": [
      {"label": "正常考试", "value": "33 分", "secondary": "绩点 0", "tone": "danger"},
      {"label": "补考", "value": "61 分", "secondary": "绩点 1.1", "tone": "success"}
    ],
    "calculation": {"label": "学分绩点贡献", "expression": "2.5 × 1.1 = 2.75"},
    "highlight": {"text": "对学期 GPA 的提升约为 +0.098", "tone": "success"}
  },
  "fallback": {"text": "大学物理补考后对学期 GPA 的提升约为 0.098。"}
}
```

后续解释正文。
````

组件声明必须位于独立块并完整闭合。不得用工具调用、工具结果、HTML、JSX、脚本、样式文本或远程 iframe
替代该协议。普通 Markdown 代码示例里出现的 `kemo-widget` 不会被执行。

## 公共信封

| 字段 | 规则 |
|------|------|
| `protocol` | 固定 `kemo-ui`；兼容输入可以省略，解析后统一补齐 |
| `schema_version` | 当前为 `1`、`"1"` 或 `"1.0"` |
| `surface` | 固定 `inline`；表示嵌入正文 |
| `id` | 每条消息内稳定，1～128 位，以字母开头，只含字母、数字、`_.:-` |
| `component` | 任意非空声明式组件名称；内置名称走专用渲染器，其他名称走 `generic-card` 通用渲染器 |
| `props` | 内置组件按对应 Schema 归一化和校验；未知名称保留 JSON 数据并以通用对象/列表/表格呈现 |
| `fallback` | 推荐 `{ "text": "..." }`；复制回复、旧客户端和降级展示使用 |

兼容解析仍接受 `kind`、`fallback_text` 以及旧式顶层 props，但新回复统一使用公共信封。

### 兼容别名

常见历史/模型别名会先归一化：

- `comparison` / `comparison_card` → `comparison-card`
- `chart` / `column_chart` → `bar-chart`
- `area-chart` → `line-chart` 并补 `area:true`
- `donut` / `donut-chart` → `pie-chart` 并补 `donut:true`
- `stats` / `metrics` → `metric-grid`
- `steps` / `step-list` → `timeline`
- `table` / `data_table` → `data-table`
- `diff` / `diff_view` → `diff-view`
- `buttons` / `button_group` → `button-group`
- `suggestions` / `follow_up` → `follow-up`
- `multi-series-chart` / `series_chart` → `series-chart`
- `stacked-chart` / `stacked_chart` → `series-chart` 并补 `stacked:true`
- `card` / `panel` → `generic-card`

`accordion` 现在有独立多项渲染器；为兼容旧输出，如果其 props 仍是 `{summary, sections}` 形状，
解析器继续把它当作 `details`。

## 组件目录与开放名称

组件名称不再使用“未知即拒绝”的封闭白名单。下列内置名称有专用可视化；常见别名会自动归一化，
例如 `line_chart`、`area-chart`、`donut-chart`、`stats`、`table`、`diff`、`buttons`。其他自定义名称也能
通过 `generic-card` 展示标量、嵌套对象、普通列表和对象数组表格。开放名称只影响声明式展示，
不解释或执行 props 中的程序代码。

### `comparison-card`

用于前后对比、方案比较、成绩变化和性能差异。

- `title?`、`description?`
- `status?: {label, tone?}`
- `items`: 1～4 项，每项为 `{label, value, secondary?, note?, tone?}`
- `calculation?: {label?, expression, note?}`
- `highlight?: {text, tone?}`

### `bar-chart`

用于非负数值的单系列柱状图。图表支持鼠标悬浮、键盘聚焦和表格数据替代。

- `title?`、`description?`
- `unit?`、`value_prefix?`、`value_suffix?`、`max?`
- `data`: 1～200 项，每项为 `{label, value, detail?, tone?, highlight?}`

`value` 必须为有限非负数；复杂多轴、负数、缩放和多系列不属于 v1。

### `line-chart`

用于趋势和连续变化；`area:true` 可显示面积填充。支持负数、键盘聚焦、Tooltip 和表格替代。

- `title?`、`description?`
- `unit?`、`value_prefix?`、`value_suffix?`、`min?`、`max?`、`area?`
- `data`: 2～200 项 `{label, value, detail?, tone?, highlight?}`

### `pie-chart`

用于组成占比，默认显示环形图；`donut:false` 可显示实心饼图。

- `title?`、`description?`、`unit?`、`donut?`
- `data`: 1～100 项 `{label, value, detail?, tone?}`

### `metric-grid`

用于 1～24 个摘要指标。

- `title?`、`description?`
- `items`: `{label, value, secondary?, delta?, tone?}`

### `progress-list`

用于任务完成度、配额和容量进度。`items` 最多 100 项，每项为
`{label, value, max?, detail?, tone?}`；省略 `max` 时按 100 计算。

### `timeline`

用于步骤、时间线和状态演进。`items` 最多 100 项，每项为
`{title, description?, time?, status?, tone?}`。

### `key-value`

用于属性、参数和结果详情。`items` 最多 100 项，每项为
`{label, value, detail?, tone?}`。

### `callout`

用于提示、结论、警告和重点说明：`{title?, content, tone?}`。

### `list`

用于有序/无序列表。`ordered?` 控制编号，`items` 最多 200 项；每项可以是字符串，也可以是
`{title, description?, tone?}`。

### `calculation`

用于 1～12 个计算步骤和最终结果。

- `title?`、`description?`
- `steps`: `{label?, expression, result?}`
- `result?: {label?, value, tone?}`

表达式按安全文本展示，不解释或执行代码。

### `data-table`

用于最多 12 列、1000 行的结构化数据，渲染器内置本地筛选、排序、分页和列显隐。

- `title?`、`description?`、`caption?`
- `columns`: `{key, label, align?, sortable?, hidden?}`，`align` 为 `left|center|right`
- `rows`: 单元格只能是字符串、有限数字、布尔值或 `null`
- `searchable?`：显示全文筛选框
- `page_size?`：每页 5～100 行，默认 20

兼容模型常见的数组输出：`columns` 可以直接是字符串数组，`rows` 可以是二维数组；前端会按列顺序
归一化为对象行，避免 `Expected object, received array`。

### `details`

用于折叠说明、推导过程或补充上下文。

- `title?`、`description?`
- `summary`
- `sections`: 1～100 项 `{title?, content}`
- `default_open?`

### `tabs` 与 `accordion`

`tabs` 用于少量互斥视图：`tabs` 为 1～20 项 `{label, content, badge?}`，`default_index?` 指定初始页。
`accordion` 用于多项独立折叠：`items` 为 1～100 项 `{title, content, tone?, default_open?}`，
`allow_multiple?` 控制是否允许同时展开多项。两者只切换当前消息内的本地展示状态。

### `diff-view` 与 `badge-group`

- `diff-view`：并排显示 `before` / `after` 文本，支持 `title?`、`description?`、`filename?`、`language?`；
  单侧最多 20,000 字。它是便于阅读的行级对照，不是完整语义 Diff 引擎。
- `badge-group`：`items` 最多 100 项 `{label, value?, tone?}`，用于标签、版本、状态或能力摘要。

### `gauge`、`series-chart`、`scatter-chart` 与 `heatmap`

- `gauge`：`value`、`min?`、`max?`、`unit?`、`label?`、`tone?`；`max` 必须大于 `min`。
- `series-chart`：`categories` 1～100 项，`series` 1～12 组 `{name, data, tone?}`；`chart_type?`
  为 `bar|line`，柱图可用 `stacked:true` 堆叠。每组数据点数量必须等于分类数；柱图不接受负值，
  多系列折线允许负值。
- `scatter-chart`：`data` 1～300 点 `{label, x, y, detail?, tone?}`，坐标允许负数；可提供
  `x_label?`、`y_label?`。
- `heatmap`：`x_labels` / `y_labels` 各 1～50 项，`cells` 最多 1000 项 `{x, y, value, label?}`；
  可提供 `min?`、`max?`、`unit?`。坐标是标签数组的零基索引。

### `calendar` 与 `kanban`

- `calendar` 是只读议程视图：`items` 最多 200 项 `{date, title, description?, tone?}`，`month?` 仅作标题，
  日期字符串由回复正文负责给出明确口径。
- `kanban` 是只读看板：`columns` 1～12 列，每列最多 100 项；列为 `{title, tone?, items}`，卡片为
  `{title, description?, meta?, tone?}`。当前版本不支持拖拽或写回任务状态。

### `button-group`、`follow-up`、`confirm` 与 `approval`

这些组件通过 `MarkdownMessage.onWidgetAction` 把**用户明确点击**转换为有限动作，不执行模型提供的代码：

- 动作只允许 `fill-input`、`send-message`、`copy`，结构为 `{type, text}`；
- `button-group.buttons` 为 1～12 项 `{label, action, tone?, disabled?}`；
- `follow-up.prompts` 为 1～12 项 `{label, text, send?}`；默认写入输入框，`send:true` 才尝试直接发送；
- `confirm` 提供 `message`、`confirm` 和本地 `cancel_label?`；取消只收起当前确认，不发送消息；
- `approval` 提供 `request`、`approve`、`reject?`，仍只产生上述文本动作，不直接调用插件、API 或工具。

会话正在生成回复时，`send-message` 不会并发启动第二个 Run，而是把文本放入输入框等待用户处理。

### `form`

用于在当前回复内收集少量信息，再由用户点击提交：

- `fields`：1～30 项，字段为 `{name, label, type, placeholder?, required?, options?, default_value?}`；
- `type` 只允许 `text|textarea|number|select|checkbox|radio`；`select` / `radio` 必须提供 `options`；
- `submit`：`{label, action, template, tone?}`，模板以 `{{field_name}}` 代入纯文本值；
- `reset_label?`：可选本地重置按钮。

表单没有任意请求地址、事件代码、文件上传或隐藏脚本；只有用户点击后才产生 `fill-input`、
`send-message` 或 `copy` 文本动作。

### `image`、`gallery` 与 `carousel`

媒体组件只接受站内根相对 URL（必须以单个 `/` 开头），不接受远程 URL、协议相对 URL、Base64、
本地绝对路径或脚本协议：

- `image`：`src`、`alt`、`caption?`，以及 `title?`、`description?`；
- `gallery`：`images` 1～50 项 `{src, alt, caption?}`，`columns?` 为 1～6；
- `carousel`：`images` 1～50 项，`start_index?` 指定初始图片。

推荐引用现有 `/api/users/.../files/.../download` 站内文件端点。组件不会自动抓取外网资源。

### 任意自定义名称 / `generic-card`

未命中内置组件时不会显示“组件不在白名单”。原始 `component` 名保存在
`props.requested_component`，通用渲染器自动处理：

- 标量 → 键值内容；
- 普通数组 → 列表；
- 对象数组 → 自动推断列的表格；
- 嵌套对象 → 分层键值结构；
- 过深数据 → 格式化 JSON 文本。

## 色调

`tone` 只允许：`neutral`、`brand`、`success`、`warning`、`danger`。色调只表达视觉语义，
状态和数值仍必须有可读文字，不得只依赖颜色。

## 生成规则

1. 只在结构化展示明显提高理解效率时生成；普通问答不要滥用。
2. 组件前后保留自然语言上下文，不能只回复一个组件。
3. `id` 在该消息中稳定且语义明确，后续流式文本不得改变已闭合组件的 ID。
4. 每个组件提供准确的 `fallback.text`；不得把 JSON 本身当作降级文本。
5. 数据必须来自当前回复已经掌握或计算的事实，不得为了图表补造数字。
6. 单条回复最多 24 个组件；单个源块最多 256 KB。
7. 不输出事件处理器、函数、HTML、JavaScript、CSS 或外部脚本。媒体只允许已存在的站内根相对 URL。
8. 展示组件只改变当前页面本地状态。动作组件只能在用户明确点击后执行 `fill-input`、`send-message`、
   `copy` 三种文本动作；不得自动发送、自动批准或直接调用工具/API。

## 流式与重试

`MarkdownMessage` 在渲染前扫描 Markdown fence。只有对应闭合 fence 到达后才解析 JSON；未闭合时不把
半截 JSON 暴露给用户，而显示“正在生成可视化组件”。Run 结束或失败快照仍未闭合时显示“组件未生成完整”。
扫描器同时跟踪普通代码 fence，因此文档示例内嵌的 `kemo-widget` 不会被误执行。

每个组件用源位置和声明 ID 保持稳定。已经闭合的组件可在后续正文继续流式到达时保留本地交互状态；
重试边界后的新尝试独立解析，不与失败尝试拼接。

## 校验与安全

前端使用 Zod 对公共信封及内置组件 props 做校验。未知组件名称进入通用渲染器；内置组件中的非法字段、
超长字符串、超出扩展后容量的数据点、非有限数值和超过 256 KB 的源块仍拒绝渲染。字段通过 React
文本节点展示，不使用组件声明提供的 HTML，也不执行声明中的任何字符串。动作字段是枚举加纯文本，
由页面已存在的输入框、发送和剪贴板函数处理；媒体地址必须通过站内根相对 URL Schema。

无效组件降级为紧凑错误卡；若声明携带合法 `fallback`，同时展示其文本。外部图片、链接及危险协议仍由
既有 Markdown 安全策略处理；组件媒体只接受站内根相对 URL。

## 历史、复制与兼容

组件源块作为 assistant `content` 的普通字符串进入现有 archive/history，不新增数据库表或协议事件。
历史恢复后重新校验、重新渲染；旧客户端最多看到 fenced JSON，不会执行代码。

网页复制智能体回复时，组件块替换为 `fallback.text` 或由已验证数据生成的文本摘要，不复制原始 JSON。
组件状态不脱离消息单独持久化；撤销、编辑重发、重新生成和删除轮次时随消息一起消失。

## 前端实现位置

- `web/frontend/src/components/Chat/inlineWidgetProtocol.ts`：扫描、Schema、兼容归一化和复制降级。
- `web/frontend/src/components/Chat/InlineWidget.tsx`：内置专用 React 组件和开放名称通用渲染器。
- `web/frontend/src/components/Chat/InlineWidget.module.css`：主题、响应式和图表布局。
- `web/frontend/src/components/Chat/MarkdownMessage.tsx`：Markdown 与组件片段的有序镶嵌。
- `web/frontend/src/pages/ChatPageView.tsx`：复制回复时使用文本降级，并把有限 Widget 动作接到输入框、
  发送函数和剪贴板。

修改组件协议后至少运行定向 Vitest、完整前端测试和生产构建；涉及消息/历史语义时补充历史与重试测试。
