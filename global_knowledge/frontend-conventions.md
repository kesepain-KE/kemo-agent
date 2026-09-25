# 前端约定与调试

本文记录 `web/frontend/` 的样式组织约定，以及在真实返工中确认有效的调试方法。手册只保留
「UI 调试先量再改」的入口，具体手法在这里。

## 样式组织

- 组件样式一律用 CSS Module（`*.module.css`），在 TSX 中通过 `styles.xxx` 引用，构建后类名带哈希。
- 主题变量集中定义在 `styles/prototype.css` 的两套主题块：
  `:root, :root[data-theme="light"]` 与 `:root[data-theme="dark"]`。
- 页面级语义别名（如 `--memory-brand: var(--brand)`）可以定义在某个页面容器的类上，
  供该页面内部复用。

## 变量链断裂：最常见也最难察觉的失效

**症状**：样式改了但页面上看不出任何变化，元素既没有背景也没有预期颜色，文字回退成继承色。

**根因模式**：声明引用了只在某个容器类上定义的别名变量，而**那个类从未挂到 DOM 上**。
此时 `var(--xxx)` 解析为空值，**整条声明失效**（不是回退到默认值，而是整条作废）：

```css
.memoryPage { --memory-brand: var(--brand); }      /* 定义了别名 */
.lifecycleInline { background: color-mix(... var(--memory-brand) ...); }
/* 若 <ModuleFrame> 没有 className={styles.memoryPage}，上面这条 background 整条消失 */
```

**排查顺序**：

1. 搜该页面/组件的类是否真的被使用：`styles.<容器类>` 在 TSX 中出现几次（0 次即命中本问题）。
2. 搜变量的定义处与引用处数量是否匹配。
3. 用全仓扫描确认变量名是否根本不存于任何定义（近似名混淆也常见，如
   `--ink` / `--text-muted` / `--orange` / `--surface-1` / `--shadow-lg` 这类"想用但没定义"的名字）。
4. 若容器组件支持 `className` 透传（如 `ModuleFrame`），补上挂载即可一次性恢复全部引用。

**预防**：变量只在确定会被渲染的容器上定义；新增别名后立刻检查该容器是否已在用。

## 布局调试：投影被父容器裁切

**症状**：卡片四周只有一圈**方形**的阴影（不是柔和扩散），圆角处也被切直。

**根因**：卡片与外层容器**尺寸齐边**，而外层设了 `overflow: hidden`。
`box-shadow` 向外扩散的部分（偏移 + 模糊半径）落到容器边界之外，被整条切掉。

**确认方法**：用 DevTools 的元素高亮（盒模型叠加层）看 —— margin 区（橙色）四角是**直角**
而 padding 区（绿色）是圆角，就说明阴影被外层裁了。或者量 `getBoundingClientRect()`，
卡片底边与外层容器底边坐标完全重合即为齐边。

**修法**：让内容区在该模式下 `overflow: visible`（条件类，不影响其他模式），
或给卡片留出大于阴影扩散距离的间距。不要用 `margin-bottom` 硬挤 —— 那只会压缩卡片高度。

## 滚动条

- CSS Module 里定义 `::-webkit-scrollbar` 不一定生效；还要处理 `::-webkit-scrollbar-button`
  （浏览器默认箭头按钮）与 `scrollbar-gutter`，否则会出现多余的轨道条或箭头。
- 出现"多出来一条方框"先确认是不是滚动条，再动样式。

## 改前端样式后的验证

1. **构建**：`npm run build`（含 `tsc -b` 类型检查）。
2. **产物核对**：在 `dist/assets/*.css` 里搜改后的规则名，确认编译结果与预期一致
   （有些写法在构建后会被改写或与相邻规则合并）。
3. **计算样式**：页面刷新后读 `getComputedStyle()`，确认目标属性真的生效，而不是只看源码。
4. **测试**：`npm test`（vitest）。涉及文案或 role/aria 的 DOM 变更要同步断言。
5. 浏览器可能缓存旧壳，验证前先强制刷新。服务端对 SPA `index.html` / 路由回退固定返回
   `no-store, no-cache, must-revalidate`，避免新构建继续引用旧哈希 Chunk；`dist/assets/` 的哈希产物则
   返回一年 `immutable` 缓存，其他静态文件使用 `no-cache`。改变这组规则时同步测试响应头。

## 文案与 DOM 契约

- 测试可能断言具体文案、`role`、`aria-label`。移动元素位置通常不影响按 role 查询的断言，
  但**改文案或删元素会**。改前先搜测试。
- 展示层剥离标点、缩短文案时，同步更新对应断言。

## 知识库编辑与预览

- 可编辑知识文件只使用一个模式切换按钮：预览态点击进入 Markdown 编辑，编辑态点击返回渲染预览；
  全局层只读文件不显示该切换按钮。
- “放大预览”始终渲染当前 `draft`，包括尚未保存的编辑内容；通过 `createPortal`
  挂载到 `document.body`，避免被右侧编辑容器的高度和 `overflow` 裁切。对话框支持关闭按钮、背景点击与
  `Escape` 关闭，并在开启期间锁定页面背景滚动。

## 正文内联组件

- 聊天正文组件只通过 `kemo-widget` 声明渲染；内置类型走 `InlineWidget` 专用组件，未知声明式名称走
  通用数据卡。开放名称兼容不代表执行任意 HTML、JSX、脚本、样式文本或远程 iframe。
- 流式渲染必须先确认 fence 闭合。不能只依赖 `streaming=false`，因为失败重试快照也会把半截正文
  收口为静态消息；未闭合组件应显示占位或未完成状态，不显示原始 JSON。
- 新组件必须使用主题变量和 CSS Module，支持暗色主题、窄屏、键盘焦点与文本/表格替代；Tooltip
  不能是读取数据的唯一途径。
- 表格排序、筛选、分页、列显隐，标签页、折叠、轮播等状态只保存在组件本地，不另建持久化表。
- 历史归档抽屉对输入会话做防御性时间倒序，避免 mock、缓存合并或旧接口返回乱序；搜索框下方的日期入口以内联
  42 格月历展开，周一为每周起点，提供上月/下月、清除和今天，并禁用上海自然日之后的未来日期。选中日期后使用
  独立 `history-sessions` 查询缓存，不能把主 `sessions` 缓存替换为日期子集。
- 需要进入会话的交互统一走 `InlineWidgetAction`，只允许 `fill-input|send-message|copy`；组件不得直接
  调 API、插件或任意回调。媒体 Schema 只接受以单个 `/` 开头的站内 URL。
- 组件字段或上限变化时同步更新 `inlineWidgetProtocol.ts` Schema、渲染组件、复制 fallback、定向测试
  以及 `global_knowledge/inline-widgets.md`。完整协议以该专题为准。

## 模块用户配置面板

- Expand 的“用户配置”Tab 和 Sense 详情底部面板共用 `ModulePanel`，只渲染后端归一化后的
  `status/config/action` 声明，不接收模块 HTML、CSS、JS、iframe 或任意回调。
- 外层宿主负责最高高度与滚动；内部用 12 列 Grid 和 CSS Container Query 实现
  `quarter/third/half/full` 宽度地板。不要把 Expand 面板重新塞进固定双列的 `.previewGrid`。
- Expand 的滚动宿主使用独立 `panelInset` 承载顶部和横向留白；底部留白必须使用位于内容之后的显式
  Grid spacer 行，不能只依赖滚动宿主或 inset 的 `padding-bottom`。当内部 Grid 的自然高度超过可视区时，
  溢出子项可能绘制到父级 padding 区，只有处于正常布局流末端的 spacer 才能稳定隔开最下方卡片和外层边框。
- `HoverPreview` 只在 `scrollHeight > clientHeight` 时出现，使用 portal、主题变量、键盘 focus 和
  `role=tooltip`；预览是补充能力，不得成为唯一的数据读取方式，masked 字段只能显示是否已设置。
- 保存接口成功后页面调用既有 refresh 端点并刷新库存与 panel query；面板值不进入 Prompt。

## 页面组合根与状态域拆分

页面文件只保留路由级查询、跨区工作流编排和最终组合。可独立演进的状态域必须下沉为 hook，重复 UI 与媒体控件必须下沉为支持组件；例如 AppShell 的 Run 注册表位于 `useChatRunRegistry.ts`，文件页的排序、媒体预览和播放器位于 `filesPageSupport.tsx`。对外页面导出与 Outlet Context 合同保持不变。
