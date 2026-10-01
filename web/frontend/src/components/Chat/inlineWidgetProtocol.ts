import { z } from 'zod'
import {
  deriveExtendedFallback,
  extendedComponentAliases,
  extendedComponentNames,
  normalizeExtendedProps,
  type ExtendedComponentName,
  type ExtendedPropsMap,
} from './inlineWidgetExtendedProtocol'
import {
  MAX_WIDGET_SOURCE_CHARS,
  MAX_WIDGETS_PER_MESSAGE,
  componentNames,
  envelopeSchema,
  propsSchemas,
  type ComponentPropsMap,
  type InlineWidgetAction,
  type InlineWidgetComponentName,
  type InlineWidgetDefinition,
  type InlineWidgetParseResult,
  type InlineWidgetTone,
} from './inlineWidgetSchemas'

export type {
  InlineWidgetAction,
  InlineWidgetComponentName,
  InlineWidgetDefinition,
  InlineWidgetParseResult,
  InlineWidgetTone,
}

const legacyComponentNames: Record<string, InlineWidgetComponentName> = {
  ...extendedComponentAliases,
  comparison: 'comparison-card',
  comparison_card: 'comparison-card',
  'comparison-card': 'comparison-card',
  bar_chart: 'bar-chart',
  chart: 'bar-chart',
  column_chart: 'bar-chart',
  'column-chart': 'bar-chart',
  'bar-chart': 'bar-chart',
  line: 'line-chart',
  line_chart: 'line-chart',
  area_chart: 'line-chart',
  'area-chart': 'line-chart',
  'line-chart': 'line-chart',
  pie: 'pie-chart',
  pie_chart: 'pie-chart',
  donut: 'pie-chart',
  donut_chart: 'pie-chart',
  'donut-chart': 'pie-chart',
  'pie-chart': 'pie-chart',
  metric_grid: 'metric-grid',
  metrics: 'metric-grid',
  stats: 'metric-grid',
  'metric-grid': 'metric-grid',
  progress: 'progress-list',
  progress_list: 'progress-list',
  'progress-list': 'progress-list',
  steps: 'timeline',
  step_list: 'timeline',
  'step-list': 'timeline',
  timeline: 'timeline',
  key_value: 'key-value',
  properties: 'key-value',
  'key-value': 'key-value',
  alert: 'callout',
  notice: 'callout',
  callout: 'callout',
  bullets: 'list',
  checklist: 'list',
  list: 'list',
  calculation: 'calculation',
  table: 'data-table',
  data_table: 'data-table',
  'data-table': 'data-table',
  accordion: 'accordion',
  details: 'details',
  tabs: 'tabs',
  tab: 'tabs',
  accordions: 'accordion',
  diff: 'diff-view',
  diff_view: 'diff-view',
  'diff-view': 'diff-view',
  badges: 'badge-group',
  badge_group: 'badge-group',
  'badge-group': 'badge-group',
  gauge: 'gauge',
  series_chart: 'series-chart',
  multi_series_chart: 'series-chart',
  'multi-series-chart': 'series-chart',
  stacked_chart: 'series-chart',
  'stacked-chart': 'series-chart',
  'series-chart': 'series-chart',
  scatter: 'scatter-chart',
  scatter_chart: 'scatter-chart',
  'scatter-chart': 'scatter-chart',
  heat_map: 'heatmap',
  heatmap: 'heatmap',
  calendar: 'calendar',
  kanban: 'kanban',
  buttons: 'button-group',
  button_group: 'button-group',
  'button-group': 'button-group',
  follow_up: 'follow-up',
  suggestions: 'follow-up',
  'follow-up': 'follow-up',
  confirm: 'confirm',
  approval: 'approval',
  form: 'form',
  image: 'image',
  gallery: 'gallery',
  carousel: 'carousel',
  card: 'generic-card',
  panel: 'generic-card',
  'generic-card': 'generic-card',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function tableColumnKey(value: unknown, index: number): string {
  if (isRecord(value) && typeof value.key === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(value.key.trim())) {
    return value.key.trim()
  }
  return `column_${index + 1}`
}

function normalizeTableProps(value: Record<string, unknown>): Record<string, unknown> {
  const columns = Array.isArray(value.columns)
    ? value.columns.map((column, index) => typeof column === 'string'
      ? { key: `column_${index + 1}`, label: column }
      : isRecord(column)
        ? { ...column, key: tableColumnKey(column, index), label: String(column.label || column.key || `列 ${index + 1}`) }
        : { key: `column_${index + 1}`, label: `列 ${index + 1}` })
    : []
  const keys = columns.map((column, index) => tableColumnKey(column, index))
  const rows = Array.isArray(value.rows)
    ? value.rows.map((row) => Array.isArray(row)
      ? Object.fromEntries(keys.map((key, index) => [key, row[index] ?? null]))
      : row)
    : value.rows
  return { ...value, columns, rows }
}

function normalizeWidgetInput(value: unknown): unknown {
  if (!isRecord(value)) return value
  const source = { ...value }
  const requestedComponent = String(source.component || source.kind || 'generic-card').trim()
  const normalizedName = requestedComponent.toLowerCase().replace(/\s+/g, '-')
  let component: InlineWidgetComponentName = legacyComponentNames[normalizedName]
    || (componentNames.includes(normalizedName as InlineWidgetComponentName)
      ? normalizedName as InlineWidgetComponentName
      : 'generic-card')
  const reserved = new Set([
    'protocol', 'schema_version', 'surface', 'id', 'component', 'kind', 'props',
    'fallback', 'fallback_text',
  ])
  let props = isRecord(source.props)
    ? { ...source.props }
    : Object.fromEntries(Object.entries(source).filter(([key]) => !reserved.has(key)))
  if (normalizedName === 'accordion' && ('summary' in props || 'sections' in props)) component = 'details'
  if (component === 'data-table') props = normalizeTableProps(props)
  if (component === 'follow-up' && !Array.isArray(props.prompts) && Array.isArray(props.options)) {
    props = {
      ...props,
      prompts: props.options.map((option) => {
        if (!isRecord(option)) return option
        const action = isRecord(option.action) ? option.action : {}
        return {
          label: option.label || option.title,
          text: option.text || option.message || action.text,
          send: option.send ?? (action.type === 'send-message' ? true : undefined),
        }
      }),
    }
    delete props.options
  }
  if (component === 'button-group' && !Array.isArray(props.buttons) && Array.isArray(props.options)) {
    props = {
      ...props,
      buttons: props.options.map((option) => {
        if (!isRecord(option)) return option
        const action = isRecord(option.action) ? option.action : null
        return {
          label: option.label || option.title,
          action: action || {
            type: option.send ? 'send-message' : 'fill-input',
            text: option.text || option.message,
          },
          tone: option.tone,
          disabled: option.disabled,
        }
      }),
    }
    delete props.options
  }
  if (component === 'line-chart' && normalizedName === 'area-chart') props.area ??= true
  if (component === 'pie-chart' && normalizedName.includes('donut')) props.donut ??= true
  if (component === 'series-chart' && normalizedName.includes('stacked')) props.stacked ??= true
  if (extendedComponentNames.includes(component as ExtendedComponentName)) {
    props = normalizeExtendedProps(component as ExtendedComponentName, props, normalizedName)
  }
  if (component === 'generic-card') props = { ...props, requested_component: requestedComponent }
  return {
    protocol: source.protocol || 'kemo-ui',
    schema_version: source.schema_version ?? 1,
    surface: source.surface || 'inline',
    id: source.id,
    component,
    props,
    fallback: source.fallback,
    fallback_text: source.fallback_text,
  }
}

function formatValue(value: string | number | boolean | null): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function deriveFallbackText(
  component: InlineWidgetComponentName,
  props: ComponentPropsMap[InlineWidgetComponentName],
): string {
  if (extendedComponentNames.includes(component as ExtendedComponentName)) {
    return deriveExtendedFallback(
      component as ExtendedComponentName,
      props as ExtendedPropsMap[ExtendedComponentName],
    )
  }
  if (component === 'comparison-card') {
    const value = props as ComponentPropsMap['comparison-card']
    const items = value.items.map((item) => `${item.label}：${formatValue(item.value)}`).join('；')
    return [value.title, items, value.highlight?.text].filter(Boolean).join('。')
  }
  if (component === 'bar-chart') {
    const value = props as ComponentPropsMap['bar-chart']
    const items = value.data.map((item) => `${item.label}：${item.value}${value.unit || ''}`).join('；')
    return [value.title, items].filter(Boolean).join('。')
  }
  if (component === 'line-chart') {
    const value = props as ComponentPropsMap['line-chart']
    const items = value.data.map((item) => `${item.label}：${item.value}${value.unit || ''}`).join('；')
    return [value.title, items].filter(Boolean).join('。')
  }
  if (component === 'pie-chart') {
    const value = props as ComponentPropsMap['pie-chart']
    const items = value.data.map((item) => `${item.label}：${item.value}${value.unit || ''}`).join('；')
    return [value.title, items].filter(Boolean).join('。')
  }
  if (component === 'metric-grid') {
    const value = props as ComponentPropsMap['metric-grid']
    const items = value.items.map((item) => `${item.label}：${formatValue(item.value)}`).join('；')
    return [value.title, items].filter(Boolean).join('。')
  }
  if (component === 'progress-list') {
    const value = props as ComponentPropsMap['progress-list']
    return [value.title, value.items.map((item) => `${item.label}：${item.value}/${item.max || 100}`).join('；')]
      .filter(Boolean).join('。')
  }
  if (component === 'timeline') {
    const value = props as ComponentPropsMap['timeline']
    return [value.title, value.items.map((item) => [item.time, item.title, item.status].filter(Boolean).join('：')).join('；')]
      .filter(Boolean).join('。')
  }
  if (component === 'key-value') {
    const value = props as ComponentPropsMap['key-value']
    return [value.title, value.items.map((item) => `${item.label}：${formatValue(item.value)}`).join('；')]
      .filter(Boolean).join('。')
  }
  if (component === 'callout') {
    const value = props as ComponentPropsMap['callout']
    return [value.title, value.content].filter(Boolean).join('。')
  }
  if (component === 'list') {
    const value = props as ComponentPropsMap['list']
    const items = value.items.map((item) => typeof item === 'string' ? item : [item.title, item.description].filter(Boolean).join('：')).join('；')
    return [value.title, items].filter(Boolean).join('。')
  }
  if (component === 'calculation') {
    const value = props as ComponentPropsMap['calculation']
    const steps = value.steps.map((step) => [step.label, step.expression, step.result].filter(Boolean).join('：')).join('；')
    const result = value.result ? `${value.result.label || '结果'}：${formatValue(value.result.value)}` : ''
    return [value.title, steps, result].filter(Boolean).join('。')
  }
  if (component === 'data-table') {
    const value = props as ComponentPropsMap['data-table']
    const rows = value.rows.slice(0, 12).map((row) => value.columns
      .map((column) => `${column.label}：${formatValue(row[column.key] ?? null)}`)
      .join('，'))
      .join('；')
    return [value.title, rows].filter(Boolean).join('。')
  }
  if (component === 'details') {
    const value = props as ComponentPropsMap['details']
    return [value.title, value.summary, ...value.sections.map((section) => [section.title, section.content].filter(Boolean).join('：'))]
      .filter(Boolean)
      .join('。')
  }
  if (component === 'tabs') {
    const value = props as ComponentPropsMap['tabs']
    return [value.title, ...value.tabs.map((tab) => `${tab.label}：${tab.content}`)].filter(Boolean).join('。')
  }
  if (component === 'accordion') {
    const value = props as ComponentPropsMap['accordion']
    return [value.title, ...value.items.map((item) => `${item.title}：${item.content}`)].filter(Boolean).join('。')
  }
  if (component === 'diff-view') {
    const value = props as ComponentPropsMap['diff-view']
    return [value.title || value.filename || '差异对比', `修改前：${value.before}`, `修改后：${value.after}`].join('。')
  }
  if (component === 'badge-group') {
    const value = props as ComponentPropsMap['badge-group']
    return [value.title, value.items.map((item) => item.value ? `${item.label}：${item.value}` : item.label).join('；')].filter(Boolean).join('。')
  }
  if (component === 'gauge') {
    const value = props as ComponentPropsMap['gauge']
    return [value.title, `${value.label || '当前值'}：${value.value}${value.unit || ''}`].filter(Boolean).join('。')
  }
  if (component === 'series-chart') {
    const value = props as ComponentPropsMap['series-chart']
    return [value.title, ...value.series.map((series) => `${series.name}：${series.data.map((item, index) => `${value.categories[index] || index + 1} ${item}${value.unit || ''}`).join('，')}`)].filter(Boolean).join('。')
  }
  if (component === 'scatter-chart') {
    const value = props as ComponentPropsMap['scatter-chart']
    return [value.title, value.data.map((item) => `${item.label}：(${item.x}, ${item.y})`).join('；')].filter(Boolean).join('。')
  }
  if (component === 'heatmap') {
    const value = props as ComponentPropsMap['heatmap']
    return [value.title, value.cells.slice(0, 100).map((cell) => `${cell.label || `${value.y_labels[cell.y] || cell.y}/${value.x_labels[cell.x] || cell.x}`}：${cell.value}${value.unit || ''}`).join('；')].filter(Boolean).join('。')
  }
  if (component === 'calendar') {
    const value = props as ComponentPropsMap['calendar']
    return [value.title || value.month, value.items.map((item) => `${item.date}：${item.title}`).join('；')].filter(Boolean).join('。')
  }
  if (component === 'kanban') {
    const value = props as ComponentPropsMap['kanban']
    return [value.title, ...value.columns.map((column) => `${column.title}：${column.items.map((item) => item.title).join('、') || '无'}`)].filter(Boolean).join('。')
  }
  if (component === 'button-group') {
    const value = props as ComponentPropsMap['button-group']
    return [value.title, value.buttons.map((button) => button.label).join('；')].filter(Boolean).join('。')
  }
  if (component === 'follow-up') {
    const value = props as ComponentPropsMap['follow-up']
    return [value.title, value.prompts.map((prompt) => prompt.text).join('；')].filter(Boolean).join('。')
  }
  if (component === 'confirm') {
    const value = props as ComponentPropsMap['confirm']
    return [value.title, value.message, `可选操作：${value.confirm.label}${value.cancel_label ? ` / ${value.cancel_label}` : ''}`].filter(Boolean).join('。')
  }
  if (component === 'approval') {
    const value = props as ComponentPropsMap['approval']
    return [value.title, value.request, `可选操作：${value.approve.label}${value.reject ? ` / ${value.reject.label}` : ''}`].filter(Boolean).join('。')
  }
  if (component === 'form') {
    const value = props as ComponentPropsMap['form']
    return [value.title, value.fields.map((field) => field.label).join('、'), `提交：${value.submit.label}`].filter(Boolean).join('。')
  }
  if (component === 'image') {
    const value = props as ComponentPropsMap['image']
    return [value.title, value.alt, value.caption].filter(Boolean).join('。')
  }
  if (component === 'gallery' || component === 'carousel') {
    const value = props as ComponentPropsMap['gallery'] | ComponentPropsMap['carousel']
    return [value.title, value.images.map((item) => item.caption || item.alt).join('；')].filter(Boolean).join('。')
  }
  const value = props as ComponentPropsMap['generic-card']
  const entries = Object.entries(value)
    .filter(([key]) => !['title', 'description', 'requested_component'].includes(key))
    .slice(0, 20)
    .map(([key, item]) => `${key}：${typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? String(item) : JSON.stringify(item)}`)
  return [value.title || value.requested_component, value.description, entries.join('；')]
    .filter(Boolean)
    .join('。')
}

function parseFailureMessage(error: z.ZodError): string {
  const first = error.issues[0]
  if (!first) return '组件数据不符合规范'
  const path = first.path.length ? `${first.path.join('.')}：` : ''
  return `${path}${first.message}`
}

export function parseInlineWidget(source: string): InlineWidgetParseResult {
  if (source.length > MAX_WIDGET_SOURCE_CHARS) {
    return { ok: false, message: '组件数据超过 256 KB 限制', fallbackText: '' }
  }
  let decoded: unknown
  try {
    decoded = JSON.parse(source)
  } catch {
    return { ok: false, message: '组件 JSON 不完整或格式错误', fallbackText: '' }
  }
  const envelope = envelopeSchema.safeParse(normalizeWidgetInput(decoded))
  if (!envelope.success) {
    const raw = isRecord(decoded) ? decoded : {}
    const fallback = typeof raw.fallback_text === 'string'
      ? raw.fallback_text.trim()
      : typeof raw.fallback === 'string'
        ? raw.fallback.trim()
        : isRecord(raw.fallback) && typeof raw.fallback.text === 'string'
          ? raw.fallback.text.trim()
          : ''
    return { ok: false, message: parseFailureMessage(envelope.error), fallbackText: fallback }
  }
  const propsResult = propsSchemas[envelope.data.component].safeParse(envelope.data.props)
  if (!propsResult.success) {
    const fallback = typeof envelope.data.fallback === 'string'
      ? envelope.data.fallback
      : envelope.data.fallback?.text || envelope.data.fallback_text || ''
    return { ok: false, message: parseFailureMessage(propsResult.error), fallbackText: fallback }
  }
  const fallbackText = typeof envelope.data.fallback === 'string'
    ? envelope.data.fallback
    : envelope.data.fallback?.text
      || envelope.data.fallback_text
      || deriveFallbackText(envelope.data.component, propsResult.data)
  return {
    ok: true,
    widget: {
      protocol: 'kemo-ui',
      schemaVersion: 1,
      surface: 'inline',
      id: envelope.data.id,
      component: envelope.data.component,
      props: propsResult.data,
      fallbackText,
    } as InlineWidgetDefinition,
  }
}

type SourceLine = { start: number; end: number; text: string }

function sourceLines(source: string): SourceLine[] {
  const lines: SourceLine[] = []
  let start = 0
  while (start < source.length) {
    const newline = source.indexOf('\n', start)
    const end = newline < 0 ? source.length : newline + 1
    const value = source.slice(start, end).replace(/\r?\n$/, '').replace(/\r$/, '')
    lines.push({ start, end, text: value })
    start = end
  }
  return lines
}

function openingFence(line: string): { character: '`' | '~'; length: number; info: string } | null {
  const match = /^ {0,3}((?:`{3,})|(?:~{3,}))[ \t]*(.*?)[ \t]*$/.exec(line)
  if (!match) return null
  const marker = match[1]
  const info = match[2].trim()
  if (marker[0] === '`' && info.includes('`')) return null
  return { character: marker[0] as '`' | '~', length: marker.length, info }
}

function closesFence(line: string, character: '`' | '~', minimumLength: number): boolean {
  const match = /^ {0,3}(`+|~+)[ \t]*$/.exec(line)
  return Boolean(match && match[1][0] === character && match[1].length >= minimumLength)
}

export type InlineWidgetSegment =
  | { type: 'markdown'; source: string; key: string }
  | { type: 'widget'; source: string; complete: boolean; blocked: boolean; key: string }

export function splitInlineWidgets(source: string): InlineWidgetSegment[] {
  if (!source.includes('kemo-widget')) return [{ type: 'markdown', source, key: 'markdown-0' }]
  const spans: Array<{ start: number; end: number; bodyStart: number; bodyEnd: number; complete: boolean }> = []
  let active: null | {
    character: '`' | '~'
    length: number
    info: string
    start: number
    bodyStart: number
  } = null
  for (const line of sourceLines(source)) {
    if (active) {
      if (closesFence(line.text, active.character, active.length)) {
        if (active.info === 'kemo-widget') {
          spans.push({
            start: active.start,
            end: line.end,
            bodyStart: active.bodyStart,
            bodyEnd: line.start,
            complete: true,
          })
        }
        active = null
      }
      continue
    }
    const opening = openingFence(line.text)
    if (!opening) continue
    active = { ...opening, start: line.start, bodyStart: line.end }
  }
  if (active?.info === 'kemo-widget') {
    spans.push({
      start: active.start,
      end: source.length,
      bodyStart: active.bodyStart,
      bodyEnd: source.length,
      complete: false,
    })
  }
  if (!spans.length) return [{ type: 'markdown', source, key: 'markdown-0' }]

  const result: InlineWidgetSegment[] = []
  let cursor = 0
  for (const [index, span] of spans.entries()) {
    if (span.start > cursor) {
      result.push({
        type: 'markdown',
        source: source.slice(cursor, span.start),
        key: `markdown-${cursor}`,
      })
    }
    result.push({
      type: 'widget',
      source: source.slice(span.bodyStart, span.bodyEnd).replace(/\r?\n$/, ''),
      complete: span.complete,
      blocked: index >= MAX_WIDGETS_PER_MESSAGE,
      key: `widget-${span.start}`,
    })
    cursor = span.end
  }
  if (cursor < source.length) {
    result.push({ type: 'markdown', source: source.slice(cursor), key: `markdown-${cursor}` })
  }
  return result
}

export function copyableAssistantText(source: string): string {
  const segments = splitInlineWidgets(source)
  if (!segments.some((segment) => segment.type === 'widget')) return source
  return segments.map((segment) => {
    if (segment.type === 'markdown') return segment.source
    if (segment.blocked) return '\n[可视化组件数量超过单条回复限制]\n'
    if (!segment.complete) return '\n[可视化组件未生成完整]\n'
    const parsed = parseInlineWidget(segment.source)
    const fallback = parsed.ok ? parsed.widget.fallbackText : parsed.fallbackText
    return `\n${fallback || '[可视化组件无法预览]'}\n`
  }).join('').replace(/\n{3,}/g, '\n\n').trim()
}
