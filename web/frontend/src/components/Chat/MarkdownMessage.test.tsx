import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MarkdownMessage } from './MarkdownMessage'

const renderMermaid = vi.fn()

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: (...args: unknown[]) => renderMermaid(...args),
  },
}))

describe('MarkdownMessage', () => {
  beforeEach(() => {
    renderMermaid.mockReset()
    renderMermaid.mockResolvedValue({ svg: '<svg aria-label="流程图"></svg>' })
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: undefined,
    })
  })

  it('renders static GFM, math, emoji and highlighted code', () => {
    const { container } = render(
      <MarkdownMessage content={'first\nsecond\n\n$E=mc^2$ :rocket:\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n- [x] done\n\n![preview](/api/users/alice/files/download?path=a.png)\n\n```json\n{"ok": true}\n```'} />,
    )

    expect(container.querySelector('br')).toBeInTheDocument()
    expect(container.querySelector('.katex')).toBeInTheDocument()
    expect(screen.getByText(/🚀/)).toBeInTheDocument()
    expect(container.querySelector('table')).toBeInTheDocument()
    expect(container.querySelector('input[type="checkbox"]')).toBeDisabled()
    expect(screen.getByRole('img', { name: 'preview' })).toHaveAttribute('loading', 'lazy')
    expect(screen.getByRole('img', { name: 'preview' })).toHaveAttribute('referrerpolicy', 'no-referrer')
    expect(container.querySelector('code.hljs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制' })).toBeInTheDocument()
  })

  it.each([false, true])('keeps single-tilde ranges as text while preserving standard strikethrough (streaming=%s)', (streaming) => {
    const { container } = render(
      <MarkdownMessage content={'1~12）：VBAT → PC13~15\n\n~~已废弃~~'} streaming={streaming} />,
    )

    expect(screen.getByText('1~12）：VBAT → PC13~15')).toBeInTheDocument()
    expect(container.querySelectorAll('del')).toHaveLength(1)
    expect(container.querySelector('del')).toHaveTextContent('已废弃')
  })

  it('copies highlighted code through the secure clipboard API', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(
      <MarkdownMessage content={'```bash\nsudo systemctl stop kemo-agent\necho done\n```'} />,
    )

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('sudo systemctl stop kemo-agent\necho done')
    })
    expect(execCommand).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('uses the synchronous textarea fallback on LAN HTTP pages', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    let selectedValue = ''
    const execCommand = vi.fn().mockImplementation(() => {
      selectedValue = document.querySelector('textarea')?.value || ''
      return true
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(<MarkdownMessage content={'```bash\nsudo systemctl restart kemo-agent\n```'} />)

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'))
    expect(writeText).not.toHaveBeenCalled()
    expect(selectedValue).toBe('sudo systemctl restart kemo-agent')
    expect(document.querySelector('textarea[aria-hidden="true"]')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('falls back when the secure clipboard API is denied', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(<MarkdownMessage content={'```text\nfallback copy\n```'} />)

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'))
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('does not add a copy button to inline code', () => {
    render(<MarkdownMessage content={'运行 `python update.py --check` 查看状态。'} />)

    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
  })

  it('filters dangerous URLs and hardens external links', () => {
    const { container } = render(
      <MarkdownMessage content={'[safe](https://example.com) [unsafe](javascript:alert(1))'} />,
    )
    const safe = screen.getByRole('link', { name: 'safe' })
    expect(safe).toHaveAttribute('target', '_blank')
    expect(safe).toHaveAttribute('rel', 'noopener noreferrer')
    expect(container.querySelector('a[href^="javascript:"]')).not.toBeInTheDocument()
  })

  it.each([false, true])('stops bare URLs at adjacent Chinese punctuation (streaming=%s)', (streaming) => {
    const { container } = render(
      <MarkdownMessage
        content={'（启动后 http://127.0.0.1:3000）手动配置 OAuth/凭据；另见 https://example.com/path，继续操作；或访问 www.example.com）查看说明。'}
        streaming={streaming}
      />,
    )

    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(3)
    expect(links[0].textContent).toBe('http://127.0.0.1:3000')
    expect(links[0]).toHaveAttribute('href', 'http://127.0.0.1:3000')
    expect(links[0].nextSibling?.textContent).toBe('）手动配置')
    expect(links[1].textContent).toBe('https://example.com/path')
    expect(links[1]).toHaveAttribute('href', 'https://example.com/path')
    expect(links[1].nextSibling?.textContent).toBe('，继续操作；或访问')
    expect(links[2].textContent).toBe('www.example.com')
    expect(links[2]).toHaveAttribute('href', 'http://www.example.com')
    expect(links[2].nextSibling?.textContent).toBe('）查看说明。')
    expect(container).toHaveTextContent('）手动配置 OAuth/凭据；另见')
    expect(container).toHaveTextContent('，继续操作；或访问')
    expect(container).toHaveTextContent('）查看说明。')
  })

  it('does not rewrite explicit Markdown links or angle-bracket autolinks', () => {
    const { container } = render(
      <MarkdownMessage content={'[地址）说明](https://example.com) <https://example.com/a，b>'} />,
    )

    expect(screen.getByRole('link', { name: '地址）说明' })).toHaveAttribute('href', 'https://example.com')
    expect(screen.getByRole('link', { name: 'https://example.com/a，b' })).toBeInTheDocument()
    expect(container.querySelectorAll('a')).toHaveLength(2)
  })

  it('keeps later bare URLs clickable when Chinese prose contains no whitespace', () => {
    const { container } = render(
      <MarkdownMessage content={'官网：https://a.example，文档：https://b.example）完成。'} />,
    )

    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(2)
    expect(links[0].textContent).toBe('https://a.example')
    expect(links[0]).toHaveAttribute('href', 'https://a.example')
    expect(links[0].nextSibling?.textContent).toBe('，文档：')
    expect(links[1].textContent).toBe('https://b.example')
    expect(links[1]).toHaveAttribute('href', 'https://b.example')
    expect(links[1].nextSibling?.textContent).toBe('）完成。')
  })

  it.each([
    '（', '）',
    '［', '］',
    '｛', '｝',
    '《', '》',
    '〈', '〉',
    '【', '】',
    '「', '」',
    '『', '』',
    '〔', '〕',
    '〖', '〗',
    '〘', '〙',
    '〚', '〛',
    '“', '”',
    '‘', '’',
  ])('stops bare URLs before the Chinese delimiter %s', (delimiter) => {
    const { container } = render(
      <MarkdownMessage content={`https://example.com${delimiter}说明`} />,
    )

    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(1)
    expect(links[0].textContent).toBe('https://example.com')
    expect(links[0]).toHaveAttribute('href', 'https://example.com')
    expect(links[0].nextSibling?.textContent).toBe(`${delimiter}说明`)
  })

  it.each([
    'https://a.example，正文nothttps://evil.example）完成。',
    'https://a.example，正文showww.example）完成。',
    'https://a.example，说明：https://）完成。',
    'https://a.example，版本：www.）完成。',
  ])('does not promote embedded or incomplete text to a later link: %s', (content) => {
    const { container } = render(<MarkdownMessage content={content} />)

    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(1)
    expect(links[0].textContent).toBe('https://a.example')
    expect(links[0]).toHaveAttribute('href', 'https://a.example')
  })

  it('does not automatically load external markdown images', () => {
    render(<MarkdownMessage content={'![tracking pixel](https://example.com/track.png)'} />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    const link = screen.getByRole('link', { name: '外部图片：tracking pixel' })
    expect(link).toHaveAttribute('href', 'https://example.com/track.png')
    expect(link).toHaveAttribute('referrerpolicy', 'no-referrer')
  })

  it('renders mermaid code blocks asynchronously', async () => {
    const { container } = render(
      <MarkdownMessage content={'```mermaid\nflowchart LR\nA-->B\n```'} />,
    )

    await waitFor(() => expect(renderMermaid).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('svg[aria-label="流程图"]')).toBeInTheDocument())
  })

  it('keeps streaming rendering lightweight', () => {
    const { container } = render(<MarkdownMessage content={'$unfinished'} streaming />)
    expect(container.querySelector('.katex')).not.toBeInTheDocument()
    expect(container.querySelector('.hljs')).not.toBeInTheDocument()
  })

  it('embeds a validated comparison widget between markdown paragraphs', () => {
    const widget = JSON.stringify({
      protocol: 'kemo-ui',
      schema_version: '1.0',
      surface: 'inline',
      id: 'physics-comparison',
      component: 'comparison-card',
      props: {
        title: '大学物理（1）',
        status: { label: '补考通过', tone: 'success' },
        items: [
          { label: '正常考试', value: '33 分', secondary: '绩点 0', tone: 'danger' },
          { label: '补考', value: '61 分', secondary: '绩点 1.1', tone: 'success' },
        ],
        calculation: { label: '学分绩点贡献', expression: '2.5 × 1.1 = 2.75' },
        highlight: { text: '对学期 GPA 的提升约为 +0.098', tone: 'success' },
      },
      fallback: { text: '大学物理补考后对学期 GPA 的提升约为 0.098。' },
    })
    render(<MarkdownMessage content={`前置说明。\n\n\`\`\`kemo-widget\n${widget}\n\`\`\`\n\n后续结论。`} />)

    expect(screen.getByText('前置说明。')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '大学物理（1）' })).toBeInTheDocument()
    expect(screen.getByText('33 分')).toBeInTheDocument()
    expect(screen.getByText('61 分')).toBeInTheDocument()
    expect(screen.getByText('2.5 × 1.1 = 2.75')).toBeInTheDocument()
    expect(screen.getByText('后续结论。')).toBeInTheDocument()
    expect(screen.queryByText(/schema_version/)).not.toBeInTheDocument()
  })

  it('shows a private streaming placeholder until the widget fence closes', () => {
    render(<MarkdownMessage content={'说明\n\n```kemo-widget\n{"schema_version":1,"id":"partial"'} streaming />)

    expect(screen.getByRole('status')).toHaveTextContent('正在生成可视化组件')
    expect(screen.queryByText(/schema_version/)).not.toBeInTheDocument()
  })

  it('marks an interrupted widget snapshot without exposing partial JSON', () => {
    render(<MarkdownMessage content={'```kemo-widget\n{"schema_version":1,"id":"partial"'} />)

    expect(screen.getByRole('status')).toHaveTextContent('组件未生成完整')
    expect(screen.queryByText(/schema_version/)).not.toBeInTheDocument()
  })

  it('renders an accessible bar chart with focus tooltip and data table fallback', () => {
    const widget = JSON.stringify({
      schema_version: 1,
      id: 'course-chart',
      component: 'bar-chart',
      props: {
        title: '各课程对学期绩点的贡献',
        unit: ' 分',
        data: [
          { label: '高等数学', value: 14.4 },
          { label: '军事理论', value: 5.6, detail: '学分绩点', highlight: true },
        ],
      },
      fallback_text: '高等数学 14.4 分，军事理论 5.6 分。',
    })
    render(<MarkdownMessage content={`\`\`\`kemo-widget\n${widget}\n\`\`\``} />)

    expect(screen.getByRole('region', { name: '各课程对学期绩点的贡献' })).toBeInTheDocument()
    const bar = screen.getByRole('img', { name: '军事理论：5.6分，学分绩点' })
    fireEvent.focus(bar)
    expect(screen.getByRole('tooltip')).toHaveTextContent('军事理论')
    expect(screen.getByRole('tooltip')).toHaveTextContent('5.6分')
    expect(screen.getByText('查看图表数据')).toBeInTheDocument()
  })

  it('keeps the tooltip for a maximum-height bar inside the chart instead of clipping it', () => {
    const widget = JSON.stringify({
      schema_version: 1,
      id: 'capacity-chart',
      component: 'bar-chart',
      props: { title: '容量上限', max: 100, data: [{ label: '表格行', value: 100, detail: '1～100 行' }] },
    })
    render(<MarkdownMessage content={`\`\`\`kemo-widget\n${widget}\n\`\`\``} />)

    fireEvent.focus(screen.getByRole('img', { name: '表格行：100，1～100 行' }))
    expect(screen.getByRole('tooltip')).toHaveStyle({ top: '8px' })
    expect(screen.getByRole('tooltip')).toHaveTextContent('1～100 行')
  })

  it('renders data-table rows supplied as arrays', () => {
    const widget = JSON.stringify({
      schema_version: 1,
      id: 'component-catalog',
      component: 'data-table',
      props: { title: '组件目录', columns: ['组件', '用途'], rows: [['line-chart', '折线图'], ['pie-chart', '饼图']] },
    })
    render(<MarkdownMessage content={`\`\`\`kemo-widget\n${widget}\n\`\`\``} />)

    expect(screen.getByRole('region', { name: '组件目录' })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: 'line-chart 折线图' })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: 'pie-chart 饼图' })).toBeInTheDocument()
  })

  it('renders new chart types and unknown components without whitelist failure', () => {
    const line = JSON.stringify({ schema_version: 1, id: 'trend', component: 'line-chart', props: { title: '趋势', data: [{ label: '一月', value: 1 }, { label: '二月', value: 3 }] } })
    const pie = JSON.stringify({ schema_version: 1, id: 'share', component: 'pie-chart', props: { title: '占比', data: [{ label: 'A', value: 3 }, { label: 'B', value: 2 }] } })
    const generic = JSON.stringify({ schema_version: 1, id: 'custom', component: 'personal-dashboard', props: { title: '个人面板', score: 98 } })
    render(<MarkdownMessage content={`\`\`\`kemo-widget\n${line}\n\`\`\`\n\n\`\`\`kemo-widget\n${pie}\n\`\`\`\n\n\`\`\`kemo-widget\n${generic}\n\`\`\``} />)

    expect(screen.getByRole('region', { name: '趋势' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '占比' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '个人面板' })).toBeInTheDocument()
    expect(screen.queryByText('组件无法预览')).not.toBeInTheDocument()
  })

  it('renders the expanded declarative component catalog', () => {
    const widgets = [
      { id: 'progress', component: 'progress-list', props: { title: '进度', items: [{ label: '完成度', value: 75 }] } },
      { id: 'timeline', component: 'timeline', props: { title: '时间线', items: [{ title: '开始', status: '完成', time: '09:00' }] } },
      { id: 'kv', component: 'key-value', props: { title: '属性', items: [{ label: '模式', value: '开放' }] } },
      { id: 'callout', component: 'callout', props: { title: '提示', content: '这是提示内容。', tone: 'warning' } },
      { id: 'list', component: 'list', props: { title: '清单', items: ['第一项', { title: '第二项', description: '说明' }] } },
    ].map((widget) => `\`\`\`kemo-widget\n${JSON.stringify({ schema_version: 1, ...widget })}\n\`\`\``).join('\n\n')
    render(<MarkdownMessage content={widgets} />)

    for (const name of ['进度', '时间线', '属性', '提示', '清单']) {
      expect(screen.getByRole(name === '提示' ? 'complementary' : 'region', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('progressbar', { name: '完成度' })).toHaveAttribute('aria-valuenow', '75')
    expect(screen.getByText('这是提示内容。')).toBeInTheDocument()
  })

  it('renders advanced layout and visualization widgets', async () => {
    const widgets = [
      { id: 'tabs', component: 'tabs', props: { title: '视图切换', tabs: [{ label: '摘要', content: '摘要内容' }, { label: '详情', content: '详情内容', badge: '2' }] } },
      { id: 'accordion', component: 'accordion', props: { title: '常见问题', items: [{ title: '为什么？', content: '因为需要。' }] } },
      { id: 'diff', component: 'diff-view', props: { title: '修改差异', before: 'old', after: 'new' } },
      { id: 'gauge', component: 'gauge', props: { title: '完成度', value: 72, unit: '%' } },
      { id: 'series', component: 'series-chart', props: { title: '多系列', chart_type: 'bar', stacked: true, categories: ['一月', '二月'], series: [{ name: 'A', data: [2, 3] }, { name: 'B', data: [1, 4] }] } },
      { id: 'scatter', component: 'scatter-chart', props: { title: '散点', data: [{ label: 'A', x: -1, y: 2 }] } },
      { id: 'heatmap', component: 'heatmap', props: { title: '热力', x_labels: ['一'], y_labels: ['甲'], cells: [{ x: 0, y: 0, value: 9 }] } },
      { id: 'calendar', component: 'calendar', props: { title: '日程', items: [{ date: '2026-09-25', title: '发布' }] } },
      { id: 'kanban', component: 'kanban', props: { title: '看板', columns: [{ title: '待办', items: [{ title: '补测试' }] }] } },
    ].map((widget) => `\`\`\`kemo-widget\n${JSON.stringify({ schema_version: 1, ...widget })}\n\`\`\``).join('\n\n')
    render(<MarkdownMessage content={widgets} />)

    await userEvent.click(screen.getByRole('tab', { name: '详情 2' }))
    expect(screen.getByText('详情内容')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '为什么？' }))
    expect(screen.getByText('因为需要。')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '修改差异' })).toHaveTextContent('old')
    expect(screen.getByRole('meter', { name: '完成度' })).toHaveAttribute('aria-valuenow', '72')
    expect(screen.getByRole('region', { name: '多系列' })).toHaveTextContent('查看图表数据')
    expect(screen.getByRole('region', { name: '热力' })).toHaveTextContent('9')
    expect(screen.getByText('补测试')).toBeInTheDocument()
  })

  it('routes explicit widget actions without executing arbitrary code', async () => {
    const onWidgetAction = vi.fn()
    const widgets = [
      { id: 'follow', component: 'follow-up', props: { prompts: [{ label: '深入分析', text: '请深入分析' }] } },
      { id: 'buttons', component: 'button-group', props: { buttons: [{ label: '复制摘要', action: { type: 'copy', text: '摘要' } }] } },
      { id: 'form', component: 'form', props: { title: '补充信息', fields: [{ name: 'course', label: '课程', type: 'text', required: true }], submit: { label: '写入输入框', action: 'fill-input', template: '课程：{{course}}' } } },
    ].map((widget) => `\`\`\`kemo-widget\n${JSON.stringify({ schema_version: 1, ...widget })}\n\`\`\``).join('\n\n')
    render(<MarkdownMessage content={widgets} onWidgetAction={onWidgetAction} />)

    await userEvent.click(screen.getByRole('button', { name: /深入分析/ }))
    expect(onWidgetAction).toHaveBeenCalledWith({ type: 'fill-input', text: '请深入分析' })
    await userEvent.click(screen.getByRole('button', { name: '复制摘要' }))
    expect(onWidgetAction).toHaveBeenCalledWith({ type: 'copy', text: '摘要' })
    await userEvent.type(screen.getByLabelText('课程 *'), '物理')
    await userEvent.click(screen.getByRole('button', { name: '写入输入框' }))
    expect(onWidgetAction).toHaveBeenCalledWith({ type: 'fill-input', text: '课程：物理' })
  })

  it('filters, sorts, paginates and hides columns in data tables', async () => {
    const widget = JSON.stringify({
      schema_version: 1,
      id: 'interactive-table',
      component: 'data-table',
      props: {
        title: '交互表格', searchable: true, page_size: 5,
        columns: [
          { key: 'name', label: '名称' }, { key: 'score', label: '分数', align: 'right' },
          { key: 'type', label: '类型' }, { key: 'owner', label: '负责人' }, { key: 'note', label: '备注' },
        ],
        rows: Array.from({ length: 7 }, (_, index) => ({ name: `项目 ${index + 1}`, score: 7 - index, type: '任务', owner: 'Kemo', note: `N${index + 1}` })),
      },
    })
    render(<MarkdownMessage content={`\`\`\`kemo-widget\n${widget}\n\`\`\``} />)

    expect(screen.getByText(/第 1\/2 页/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('项目 7')).toBeInTheDocument()
    await userEvent.type(screen.getByPlaceholderText('搜索当前表格'), '项目 2')
    expect(screen.getByText('项目 2')).toBeInTheDocument()
    expect(screen.queryByText('项目 7')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText(/显示列/))
    await userEvent.click(screen.getByRole('checkbox', { name: '备注' }))
    expect(screen.queryByRole('columnheader', { name: '备注' })).not.toBeInTheDocument()
    await userEvent.clear(screen.getByPlaceholderText('搜索当前表格'))
    await userEvent.click(screen.getByRole('button', { name: '分数' }))
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('项目 7')
  })

  it('falls back to a compact error card for invalid widget data', () => {
    render(<MarkdownMessage content={'```kemo-widget\n{"schema_version":1,"id":"broken","component":"bar-chart","props":{"data":[]},"fallback_text":"暂无图表数据"}\n```'} />)

    expect(screen.getByText('组件无法预览')).toBeInTheDocument()
    expect(screen.getByText('暂无图表数据')).toBeInTheDocument()
  })

  it('caps a single reply at twenty-four rendered widgets', () => {
    const content = Array.from({ length: 25 }, (_, index) => `\`\`\`kemo-widget\n${JSON.stringify({
      schema_version: 1,
      id: `metric-${index + 1}`,
      component: 'metric-grid',
      props: { items: [{ label: '序号', value: index + 1 }] },
    })}\n\`\`\``).join('\n')
    render(<MarkdownMessage content={content} />)

    expect(screen.getAllByRole('region', { name: '指标摘要' })).toHaveLength(24)
    expect(screen.getByText('组件数量超过限制')).toBeInTheDocument()
  })
})
