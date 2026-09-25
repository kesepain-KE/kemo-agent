import { describe, expect, it } from 'vitest'
import {
  copyableAssistantText,
  parseInlineWidget,
  splitInlineWidgets,
} from './inlineWidgetProtocol'

const comparisonSource = JSON.stringify({
  protocol: 'kemo-ui',
  schema_version: '1.0',
  surface: 'inline',
  id: 'physics-1-comparison',
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

describe('inline widget protocol', () => {
  it('parses a versioned comparison widget', () => {
    const result = parseInlineWidget(comparisonSource)

    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.message)
    expect(result.widget.component).toBe('comparison-card')
    expect(result.widget.id).toBe('physics-1-comparison')
    expect(result.widget.fallbackText).toContain('0.098')
  })

  it('accepts the compact legacy kind form and derives fallback text', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'metrics-1',
      kind: 'metric_grid',
      title: '运行摘要',
      items: [{ label: '通过', value: 12, tone: 'success' }],
    }))

    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.message)
    expect(result.widget.component).toBe('metric-grid')
    expect(result.widget.fallbackText).toBe('运行摘要。通过：12')
  })

  it('splits complete widgets from surrounding markdown', () => {
    const segments = splitInlineWidgets(`上文\n\n\`\`\`kemo-widget\n${comparisonSource}\n\`\`\`\n\n下文`)

    expect(segments).toHaveLength(3)
    expect(segments[0]).toMatchObject({ type: 'markdown', source: '上文\n\n' })
    expect(segments[1]).toMatchObject({ type: 'widget', complete: true, source: comparisonSource })
    expect(segments[2]).toMatchObject({ type: 'markdown', source: '\n下文' })
  })

  it('keeps an unclosed widget isolated from the visible markdown stream', () => {
    const segments = splitInlineWidgets('说明\n\n```kemo-widget\n{"schema_version":1')

    expect(segments).toHaveLength(2)
    expect(segments[1]).toMatchObject({ type: 'widget', complete: false })
  })

  it('does not detect widget-looking text inside a normal fenced code block', () => {
    const source = '````markdown\n```kemo-widget\n{"id":"example"}\n```\n````'

    expect(splitInlineWidgets(source)).toEqual([{ type: 'markdown', source, key: 'markdown-0' }])
  })

  it('replaces widget JSON with fallback text when copying a reply', () => {
    const copied = copyableAssistantText(`计算如下：\n\n\`\`\`kemo-widget\n${comparisonSource}\n\`\`\`\n\n计算完成。`)

    expect(copied).toContain('计算如下')
    expect(copied).toContain('大学物理补考后对学期 GPA 的提升约为 0.098。')
    expect(copied).toContain('计算完成')
    expect(copied).not.toContain('schema_version')
  })

  it('marks widgets beyond the per-message limit and omits their JSON when copying', () => {
    const source = Array.from({ length: 25 }, (_, index) => `\`\`\`kemo-widget\n${JSON.stringify({
      schema_version: 1,
      id: `metric-${index + 1}`,
      component: 'metric-grid',
      props: { items: [{ label: '序号', value: index + 1 }] },
      fallback_text: `组件 ${index + 1}`,
    })}\n\`\`\``).join('\n')
    const widgets = splitInlineWidgets(source).filter((segment) => segment.type === 'widget')

    expect(widgets).toHaveLength(25)
    expect(widgets.slice(0, 24).every((segment) => segment.type === 'widget' && !segment.blocked)).toBe(true)
    expect(widgets[24]).toMatchObject({ type: 'widget', blocked: true })
    expect(copyableAssistantText(source)).toContain('可视化组件数量超过单条回复限制')
    expect(copyableAssistantText(source)).not.toContain('schema_version')
  })

  it('normalizes data-table columns and array rows from common model output', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'table-array-rows',
      component: 'data-table',
      props: {
        title: '白名单目录',
        columns: ['组件', '用途'],
        rows: [['bar-chart', '柱状图'], ['timeline', '时间线']],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'data-table') throw new Error('data table normalization failed')
    expect(result.widget.props.columns).toEqual([
      { key: 'column_1', label: '组件' },
      { key: 'column_2', label: '用途' },
    ])
    expect(result.widget.props.rows[0]).toEqual({ column_1: 'bar-chart', column_2: '柱状图' })
  })

  it('accepts sortable table columns and strips unsupported presentation hints', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: '1.0',
      id: 'memory-tiers',
      component: 'data-table',
      props: {
        title: '临时记忆四档',
        searchable: true,
        page_size: 10,
        columns: [
          { key: 'tier', label: '层级', sortable: true, filterable: true, width: 180 },
          { key: 'promote', label: '晋升权重阈值', align: 'right', sortable: true },
        ],
        rows: [{ tier: 'seven_days', promote: 3 }],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'data-table') throw new Error('sortable table parsing failed')
    expect(result.widget.props.columns[0]).toEqual({ key: 'tier', label: '层级', sortable: true })
  })

  it('opens unknown declarative component names through the generic renderer', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'custom-dashboard',
      component: 'personal-dashboard',
      props: { title: '个人面板', score: 98, tags: ['稳定', '快速'] },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'generic-card') throw new Error('generic component normalization failed')
    expect(result.widget.props.requested_component).toBe('personal-dashboard')
    expect(result.widget.props.score).toBe(98)
  })

  it('parses the advanced layout, visualization, action and media catalog', () => {
    const samples = [
      { id: 'tabs-1', component: 'tabs', props: { tabs: [{ label: '摘要', content: '内容' }] } },
      { id: 'accordion-1', component: 'accordion', props: { items: [{ title: '详情', content: '内容' }] } },
      { id: 'diff-1', component: 'diff-view', props: { before: 'a', after: 'b' } },
      { id: 'badges-1', component: 'badge-group', props: { items: [{ label: '稳定', tone: 'success' }] } },
      { id: 'gauge-1', component: 'gauge', props: { value: 73, max: 100 } },
      { id: 'series-1', component: 'series-chart', props: { chart_type: 'bar', stacked: true, categories: ['一月'], series: [{ name: 'A', data: [2] }, { name: 'B', data: [3] }] } },
      { id: 'scatter-1', component: 'scatter-chart', props: { data: [{ label: '样本', x: -1, y: 2 }] } },
      { id: 'heatmap-1', component: 'heatmap', props: { x_labels: ['一'], y_labels: ['甲'], cells: [{ x: 0, y: 0, value: 1 }] } },
      { id: 'calendar-1', component: 'calendar', props: { items: [{ date: '2026-09-25', title: '发布' }] } },
      { id: 'kanban-1', component: 'kanban', props: { columns: [{ title: '待办', items: [] }] } },
      { id: 'buttons-1', component: 'button-group', props: { buttons: [{ label: '复制', action: { type: 'copy', text: '内容' } }] } },
      { id: 'follow-1', component: 'follow-up', props: { prompts: [{ label: '继续', text: '继续分析' }] } },
      { id: 'confirm-1', component: 'confirm', props: { message: '确认吗？', confirm: { label: '确认', action: { type: 'send-message', text: '确认' } } } },
      { id: 'approval-1', component: 'approval', props: { request: '批准方案', approve: { label: '批准', action: { type: 'send-message', text: '批准' } } } },
      { id: 'form-1', component: 'form', props: { fields: [{ name: 'course', label: '课程', type: 'text', required: true }], submit: { label: '提交', action: 'fill-input', template: '课程：{{course}}' } } },
      { id: 'image-1', component: 'image', props: { src: '/api/users/a/files/download?path=a.png', alt: '图片' } },
      { id: 'gallery-1', component: 'gallery', props: { images: [{ src: '/api/a.png', alt: '图片 A' }] } },
      { id: 'carousel-1', component: 'carousel', props: { images: [{ src: '/api/a.png', alt: '图片 A' }] } },
    ]

    for (const sample of samples) {
      const result = parseInlineWidget(JSON.stringify({ schema_version: 1, ...sample }))
      expect(result.ok, sample.component).toBe(true)
      if (result.ok) expect(result.widget.component).toBe(sample.component)
    }
  })

  it('keeps media widgets on same-origin relative URLs', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'external-image',
      component: 'image',
      props: { src: 'https://example.com/a.png', alt: '外部图片' },
    }))

    expect(result.ok).toBe(false)
  })

  it('keeps the legacy accordion alias compatible with details-shaped props', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'legacy-accordion',
      component: 'accordion',
      props: { summary: '旧式摘要', sections: [{ content: '旧式正文' }] },
    }))

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.widget.component).toBe('details')
  })

  it('rejects oversized or executable-shaped component payloads', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'unsafe-1',
      component: 'comparison-card',
      props: {
        title: '<script>alert(1)</script>',
        items: [{ label: '测试', value: '1', onClick: 'alert(1)' }],
      },
    }))

    expect(result.ok).toBe(false)
  })
})
