import { describe, expect, it } from 'vitest'
import {
  copyableAssistantText,
  parseInlineWidget,
  splitInlineWidgets,
  type InlineWidgetDefinition,
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
  it('parses the native educational interactive visual and derives a readable fallback', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'solar-system-visual',
      component: 'orbit',
      props: {
        kind: 'orbit',
        title: '行星相对运动',
        description: '拖动时间轴或调整速度观察周期差异。',
        center: { label: '太阳' },
        bodies: [
          { label: '地球', orbit: 3, period: 1, color: 'blue', detail: '一年完成一周。' },
          { label: '火星', orbit: 4, period: 1.88, color: 'rose' },
        ],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error(result.message)
    expect(result.widget.component).toBe('interactive-visual')
    expect(result.widget.fallbackText).toContain('地球：第 3 轨道')
  })

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

  it('normalizes legacy follow-up options from persisted model output', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: '1.0',
      id: 'legacy-follow-up-options',
      component: 'follow-up',
      props: {
        title: '下一步可以',
        options: [
          { label: '继续分析', message: '请继续分析当前问题' },
          { label: '直接执行', action: { type: 'send-message', text: '确认执行' } },
        ],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'follow-up') throw new Error('legacy follow-up normalization failed')
    expect(result.widget.props.prompts).toEqual([
      { label: '继续分析', text: '请继续分析当前问题', send: undefined },
      { label: '直接执行', text: '确认执行', send: true },
    ])
  })

  it('parses the composable, location, media, commerce and feedback catalog', () => {
    const samples = [
      {
        id: 'ui-card-1', component: 'ui-card', props: {
          title: '组合卡片',
          nodes: [
            { type: 'row', children: [{ type: 'icon', name: 'sparkles', label: '智能摘要' }, { type: 'badge', label: '状态', value: '正常', tone: 'success' }] },
            { type: 'markdown', text: '**重点**：组件可组合。' },
            { type: 'text-input', name: 'topic', label: '主题', default_value: '组件' },
            { type: 'button', label: '继续', action: { type: 'fill-input', text: '继续分析 {{topic}}' } },
          ],
        },
      },
      { id: 'map-1', component: 'map', props: { points: [{ label: '上海', latitude: 31.2304, longitude: 121.4737 }] } },
      { id: 'audio-1', component: 'audio', props: { src: '/api/media/a.mp3', caption: '音频' } },
      { id: 'files-1', component: 'file-list', props: { files: [{ name: '报告.pdf', url: '/api/files/report.pdf' }] } },
      { id: 'products-1', component: 'product-grid', props: { products: [{ name: '组件包', price: '¥99' }] } },
      { id: 'order-1', component: 'order-summary', props: { items: [{ label: '组件包', quantity: 2, unit_price: 99 }] } },
      { id: 'poll-1', component: 'poll', props: { title: '选择方向', options: [{ label: 'A', value: 'a' }, { label: 'B', value: 'b' }], submit: { label: '提交', action: 'fill-input', template: '选择：{{selection}}' } } },
      { id: 'rating-1', component: 'rating', props: { label: '满意度', max: 5, submit: { label: '提交评分', action: 'fill-input', template: '评分：{{rating}}' } } },
    ]

    for (const sample of samples) {
      const result = parseInlineWidget(JSON.stringify({ schema_version: 1, ...sample }))
      expect(result.ok, sample.component).toBe(true)
    }
  })

  it('normalizes object submit actions from interactive component output', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'poll-object-action',
      component: 'poll',
      props: {
        title: '下一步方向',
        options: [{ label: '图表', value: 'charts' }, { label: '交互', value: 'interactive' }],
        submit: {
          label: '提交',
          action: { type: 'fill-input', text: '下次想看：{{selection}}' },
        },
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'poll') throw new Error('poll action normalization failed')
    expect(result.widget.props.submit).toMatchObject({
      action: 'fill-input',
      template: '下次想看：{{selection}}',
    })
  })

  it('fills safe labels and templates for legacy confirmation feedback', () => {
    const confirm = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'legacy-confirm',
      component: 'confirm',
      props: { message: '是否继续？', confirm: { action: { type: 'send-message', text: '继续' } } },
    }))
    const approval = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'legacy-approval',
      component: 'approval',
      props: { request: '请批准', approve: { action: { type: 'send-message', text: '批准' } } },
    }))

    expect(confirm.ok).toBe(true)
    expect(approval.ok).toBe(true)
    if (!confirm.ok || !approval.ok) throw new Error('feedback normalization failed')
    const confirmWidget = confirm.widget as Extract<InlineWidgetDefinition, { component: 'confirm' }>
    const approvalWidget = approval.widget as Extract<InlineWidgetDefinition, { component: 'approval' }>
    expect(confirmWidget.props.confirm.label).toBe('确认')
    expect(approvalWidget.props.approve.label).toBe('批准')
  })

  it('normalizes string options, object submit actions and wrapped ui-card nodes', () => {
    const form = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'legacy-form',
      component: 'form',
      props: {
        fields: [{ id: 'device', label: '设备', type: 'select', options: ['电脑', '手机'] }],
        submit: { label: '查询', action: { type: 'fill-input', template: '查询：{{device}}' } },
      },
    }))
    const card = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'wrapped-ui-card',
      component: 'ui-card',
      props: {
        nodes: [{
          type: 'form',
          props: {
            fields: [{ name: 'topic', label: '主题', type: 'text' }],
            submit: { label: '填入', action: { type: 'fill-input', text: '分析 {{topic}}' } },
          },
        }],
      },
    }))

    expect(form.ok).toBe(true)
    expect(card.ok).toBe(true)
    if (!form.ok || !card.ok) throw new Error('component normalization failed')
    const formWidget = form.widget as Extract<InlineWidgetDefinition, { component: 'form' }>
    const cardWidget = card.widget as Extract<InlineWidgetDefinition, { component: 'ui-card' }>
    expect(formWidget.props.fields[0].options).toEqual([
      { label: '电脑', value: '电脑' },
      { label: '手机', value: '手机' },
    ])
    expect(formWidget.props.submit).toMatchObject({ action: 'fill-input', template: '查询：{{device}}' })
    const firstNode = cardWidget.props.nodes[0]
    expect(firstNode).toMatchObject({ type: 'box' })
    if ('children' in firstNode) expect(firstNode.children.at(-1)).toMatchObject({ type: 'button', label: '填入' })
  })

  it('normalizes common layout aliases in ui-card nodes', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'ui-card-layout-aliases',
      component: 'ui-card',
      props: {
        title: '组合卡片',
        nodes: [{
          type: 'stack',
          children: [
            { type: 'text-input', name: 'topic', label: '主题', default_value: '组件' },
            { type: 'slider', name: 'depth', label: '深度', min: 1, max: 5, default_value: 3 },
            { type: 'button', label: '继续', action: { type: 'fill-input', text: '继续分析 {{topic}}，深度 {{depth}}' } },
          ],
        }],
      },
    }))

    expect(result.ok).toBe(true)
  })

  it('normalizes common form wrappers and field definitions in ui-card nodes', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'ui-card-form-wrapper',
      component: 'ui-card',
      props: {
        title: '组合卡片',
        description: '输入主题与深度，点击按钮把指令文本填进输入框。',
        nodes: [
          {
            type: 'form',
            fields: [
              { name: 'topic', label: '主题', type: 'text', default: '组件' },
              { name: 'depth', label: '深度', type: 'number', min: 1, max: 5, default: 3 },
            ],
          },
          {
            type: 'button',
            label: '继续',
            action: 'fill-input',
            text: '继续分析 {{topic}}，深度 {{depth}}',
          },
        ],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'ui-card') throw new Error('ui-card form normalization failed')
    expect(result.widget.props.nodes[0]).toMatchObject({
      type: 'box',
      children: [
        { type: 'text-input', name: 'topic', default_value: '组件' },
        { type: 'number-input', name: 'depth', default_value: 3 },
      ],
    })
    expect(result.widget.props.nodes[1]).toMatchObject({
      type: 'button',
      action: { type: 'fill-input', text: '继续分析 {{topic}}，深度 {{depth}}' },
    })
  })

  it('normalizes device query cards whose input type and options use legacy aliases', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'device-query-card',
      component: 'ui-card',
      props: {
        title: '设备查询',
        description: '输入设备并点击查询。',
        nodes: [
          {
            type: 'input',
            input_type: 'select',
            name: 'device',
            label: '设备',
            options: ['电脑', '手机'],
          },
          {
            type: 'button',
            label: '查询',
            action: { type: 'fill-input', template: '查询设备：{{device}}' },
          },
        ],
      },
    }))

    expect(result.ok).toBe(true)
    if (!result.ok || result.widget.component !== 'ui-card') throw new Error('device query normalization failed')
    expect(result.widget.props.nodes[0]).toMatchObject({
      type: 'select',
      name: 'device',
      options: [
        { label: '电脑', value: '电脑' },
        { label: '手机', value: '手机' },
      ],
    })
  })

  it('normalizes numeric product prices and unlabeled order actions', () => {
    const product = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'numeric-product',
      component: 'product-grid',
      props: {
        products: [{ name: '组件包', price: 199 }],
      },
    }))
    const order = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'unlabeled-order-action',
        component: 'order-summary',
        props: {
          items: [{ label: '组件包', quantity: 1, unit_price: 199 }],
        actions: [{ type: 'send-message', text: '确认订单' }],
        },
      }))

    expect(product.ok).toBe(true)
    expect(order.ok).toBe(true)
    if (!product.ok || !order.ok) throw new Error('commerce normalization failed')
    if (product.widget.component !== 'product-grid' || order.widget.component !== 'order-summary') {
      throw new Error('commerce component normalization failed')
    }
    expect(product.widget.props.products[0].price).toBe('199')
    expect(order.widget.props.actions?.[0]).toMatchObject({
      label: '确认订单',
      action: { type: 'send-message', text: '确认订单' },
    })
  })

  it('rejects external URLs in the extended media catalog', () => {
    const result = parseInlineWidget(JSON.stringify({
      schema_version: 1,
      id: 'external-video',
      component: 'video',
      props: { src: 'https://example.com/video.mp4' },
    }))

    expect(result.ok).toBe(false)
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
