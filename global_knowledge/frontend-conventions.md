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
5. 浏览器可能缓存旧壳，验证前先强制刷新。

## 文案与 DOM 契约

- 测试可能断言具体文案、`role`、`aria-label`。移动元素位置通常不影响按 role 查询的断言，
  但**改文案或删元素会**。改前先搜测试。
- 展示层剥离标点、缩短文案时，同步更新对应断言。
