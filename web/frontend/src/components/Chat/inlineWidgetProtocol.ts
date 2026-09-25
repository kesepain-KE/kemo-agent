import { z } from 'zod'

const MAX_WIDGET_SOURCE_CHARS = 262_144
const MAX_WIDGETS_PER_MESSAGE = 24
const MAX_TEXT_CHARS = 20_000
const MAX_TITLE_CHARS = 500

const toneSchema = z.enum(['neutral', 'brand', 'success', 'warning', 'danger'])
const displayValueSchema = z.union([
  z.string().max(160),
  z.number().finite(),
])
const shortTextSchema = z.string().trim().min(1).max(MAX_TITLE_CHARS)
const bodyTextSchema = z.string().trim().min(1).max(MAX_TEXT_CHARS)

const comparisonPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  status: z.object({
    label: z.string().trim().min(1).max(80),
    tone: toneSchema.optional(),
  }).strict().optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: displayValueSchema,
    secondary: z.string().trim().max(240).optional(),
    note: z.string().trim().max(500).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(4),
  calculation: z.object({
    label: z.string().trim().max(240).optional(),
    expression: z.string().trim().min(1).max(500),
    note: z.string().trim().max(500).optional(),
  }).strict().optional(),
  highlight: z.object({
    text: bodyTextSchema,
    tone: toneSchema.optional(),
  }).strict().optional(),
}).strict()

const barChartPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  unit: z.string().trim().max(40).optional(),
  value_prefix: z.string().max(20).optional(),
  value_suffix: z.string().max(20).optional(),
  max: z.number().finite().positive().optional(),
  data: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: z.number().finite().min(0).max(1_000_000_000_000),
    detail: z.string().trim().max(300).optional(),
    tone: toneSchema.optional(),
    highlight: z.boolean().optional(),
  }).strict()).min(1).max(200),
}).strict()

const lineChartPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  unit: z.string().trim().max(40).optional(),
  value_prefix: z.string().max(20).optional(),
  value_suffix: z.string().max(20).optional(),
  min: z.number().finite().optional(),
  max: z.number().finite().optional(),
  area: z.boolean().optional(),
  data: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: z.number().finite().min(-1_000_000_000_000).max(1_000_000_000_000),
    detail: z.string().trim().max(300).optional(),
    tone: toneSchema.optional(),
    highlight: z.boolean().optional(),
  }).strict()).min(2).max(200),
}).strict()

const pieChartPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  unit: z.string().trim().max(40).optional(),
  donut: z.boolean().optional(),
  data: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: z.number().finite().min(0).max(1_000_000_000_000),
    detail: z.string().trim().max(300).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(100),
}).strict()

const metricGridPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: displayValueSchema,
    secondary: z.string().trim().max(240).optional(),
    delta: z.string().trim().max(120).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(24),
}).strict()

const progressListPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(160),
    value: z.number().finite(),
    max: z.number().finite().positive().optional(),
    detail: z.string().trim().max(500).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(100),
}).strict()

const timelinePropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    title: z.string().trim().min(1).max(200),
    description: z.string().trim().max(2_000).optional(),
    time: z.string().trim().max(160).optional(),
    status: z.string().trim().max(100).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(100),
}).strict()

const keyValuePropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(160),
    value: displayValueSchema,
    detail: z.string().trim().max(1_000).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(100),
}).strict()

const calloutPropsSchema = z.object({
  title: shortTextSchema.optional(),
  content: bodyTextSchema,
  tone: toneSchema.optional(),
}).strict()

const listPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  ordered: z.boolean().optional(),
  items: z.array(z.union([
    z.string().trim().min(1).max(2_000),
    z.object({
      title: z.string().trim().min(1).max(200),
      description: z.string().trim().max(2_000).optional(),
      tone: toneSchema.optional(),
    }).strict(),
  ])).min(1).max(200),
}).strict()

const calculationPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  steps: z.array(z.object({
    label: z.string().trim().max(160).optional(),
    expression: z.string().trim().min(1).max(500),
    result: z.string().trim().max(240).optional(),
  }).strict()).min(1).max(12),
  result: z.object({
    label: z.string().trim().max(160).optional(),
    value: displayValueSchema,
    tone: toneSchema.optional(),
  }).strict().optional(),
}).strict()

const tableCellSchema = z.union([
  z.string().max(1_000),
  z.number().finite(),
  z.boolean(),
  z.null(),
])
const dataTablePropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  caption: z.string().trim().max(240).optional(),
  columns: z.array(z.object({
    key: z.string().trim().regex(/^[A-Za-z0-9_.-]{1,64}$/),
    label: z.string().trim().min(1).max(120),
    align: z.enum(['left', 'center', 'right']).optional(),
    sortable: z.boolean().optional(),
    hidden: z.boolean().optional(),
  }).strip()).min(1).max(12),
  rows: z.array(z.record(tableCellSchema)).min(1).max(1_000),
  searchable: z.boolean().optional(),
  page_size: z.number().int().min(5).max(100).optional(),
}).strict()

const detailsPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  summary: z.string().trim().min(1).max(240),
  sections: z.array(z.object({
    title: z.string().trim().max(160).optional(),
    content: bodyTextSchema,
  }).strict()).min(1).max(100),
  default_open: z.boolean().optional(),
}).strict()

const tabsPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  tabs: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    content: bodyTextSchema,
    badge: z.string().trim().max(40).optional(),
  }).strict()).min(1).max(20),
  default_index: z.number().int().min(0).max(19).optional(),
}).strict()

const accordionPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    title: z.string().trim().min(1).max(200),
    content: bodyTextSchema,
    tone: toneSchema.optional(),
    default_open: z.boolean().optional(),
  }).strict()).min(1).max(100),
  allow_multiple: z.boolean().optional(),
}).strict()

const diffViewPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  filename: z.string().trim().max(500).optional(),
  language: z.string().trim().max(80).optional(),
  before: z.string().max(MAX_TEXT_CHARS),
  after: z.string().max(MAX_TEXT_CHARS),
}).strict()

const badgeGroupPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: z.string().trim().max(160).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(100),
}).strict()

const gaugePropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  label: z.string().trim().max(160).optional(),
  value: z.number().finite(),
  min: z.number().finite().optional(),
  max: z.number().finite().optional(),
  unit: z.string().trim().max(40).optional(),
  tone: toneSchema.optional(),
}).strict().refine((value) => (value.max ?? 100) > (value.min ?? 0), {
  message: 'max 必须大于 min',
})

const seriesChartPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  chart_type: z.enum(['bar', 'line']).optional(),
  stacked: z.boolean().optional(),
  unit: z.string().trim().max(40).optional(),
  categories: z.array(z.string().trim().min(1).max(120)).min(1).max(100),
  series: z.array(z.object({
    name: z.string().trim().min(1).max(120),
    data: z.array(z.number().finite().min(-1_000_000_000_000).max(1_000_000_000_000)).min(1).max(100),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(12),
}).strict().superRefine((value, context) => {
  for (const [index, series] of value.series.entries()) {
    if (series.data.length !== value.categories.length) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['series', index, 'data'], message: '数据点数量必须与 categories 一致' })
    }
    if ((value.chart_type ?? 'bar') === 'bar' && series.data.some((item) => item < 0)) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ['series', index, 'data'], message: '柱状多系列图不接受负值' })
    }
  }
})

const scatterChartPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  x_label: z.string().trim().max(80).optional(),
  y_label: z.string().trim().max(80).optional(),
  data: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    x: z.number().finite().min(-1_000_000_000_000).max(1_000_000_000_000),
    y: z.number().finite().min(-1_000_000_000_000).max(1_000_000_000_000),
    detail: z.string().trim().max(300).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(300),
}).strict()

const heatmapPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  x_labels: z.array(z.string().trim().min(1).max(80)).min(1).max(50),
  y_labels: z.array(z.string().trim().min(1).max(80)).min(1).max(50),
  cells: z.array(z.object({
    x: z.number().int().min(0).max(49),
    y: z.number().int().min(0).max(49),
    value: z.number().finite(),
    label: z.string().trim().max(120).optional(),
  }).strict()).min(1).max(1_000),
  min: z.number().finite().optional(),
  max: z.number().finite().optional(),
  unit: z.string().trim().max(40).optional(),
}).strict()

const calendarPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  month: z.string().trim().max(80).optional(),
  items: z.array(z.object({
    date: z.string().trim().min(1).max(40),
    title: z.string().trim().min(1).max(200),
    description: z.string().trim().max(1_000).optional(),
    tone: toneSchema.optional(),
  }).strict()).min(1).max(200),
}).strict()

const kanbanPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  columns: z.array(z.object({
    title: z.string().trim().min(1).max(120),
    tone: toneSchema.optional(),
    items: z.array(z.object({
      title: z.string().trim().min(1).max(200),
      description: z.string().trim().max(1_000).optional(),
      meta: z.string().trim().max(160).optional(),
      tone: toneSchema.optional(),
    }).strict()).max(100),
  }).strict()).min(1).max(12),
}).strict()

const actionTypeSchema = z.enum(['fill-input', 'send-message', 'copy'])
const widgetActionSchema = z.object({
  type: actionTypeSchema,
  text: z.string().min(1).max(20_000),
}).strict()
const actionButtonSchema = z.object({
  label: z.string().trim().min(1).max(80),
  action: widgetActionSchema,
  tone: toneSchema.optional(),
  disabled: z.boolean().optional(),
}).strict()

const buttonGroupPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  buttons: z.array(actionButtonSchema).min(1).max(12),
}).strict()

const followUpPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  prompts: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    text: z.string().trim().min(1).max(4_000),
    send: z.boolean().optional(),
  }).strict()).min(1).max(12),
}).strict()

const confirmPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  message: bodyTextSchema,
  confirm: actionButtonSchema,
  cancel_label: z.string().trim().min(1).max(80).optional(),
}).strict()

const approvalPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  request: bodyTextSchema,
  approve: actionButtonSchema,
  reject: actionButtonSchema.optional(),
}).strict()

const formFieldSchema = z.object({
  name: z.string().trim().regex(/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/),
  label: z.string().trim().min(1).max(120),
  type: z.enum(['text', 'textarea', 'number', 'select', 'checkbox', 'radio']),
  placeholder: z.string().max(240).optional(),
  required: z.boolean().optional(),
  options: z.array(z.object({
    label: z.string().trim().min(1).max(120),
    value: z.string().max(240),
  }).strict()).max(100).optional(),
  default_value: z.union([z.string().max(2_000), z.number().finite(), z.boolean()]).optional(),
}).strict().refine((value) => !['select', 'radio'].includes(value.type) || Boolean(value.options?.length), {
  message: 'select/radio 字段必须提供 options',
})
const formPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  fields: z.array(formFieldSchema).min(1).max(30),
  submit: z.object({
    label: z.string().trim().min(1).max(80),
    action: actionTypeSchema,
    template: z.string().min(1).max(20_000),
    tone: toneSchema.optional(),
  }).strict(),
  reset_label: z.string().trim().min(1).max(80).optional(),
}).strict()

const mediaItemSchema = z.object({
  src: z.string().trim().regex(/^\/(?!\/)[^\s]{1,2000}$/, '只允许站内相对 URL'),
  alt: z.string().trim().min(1).max(500),
  caption: z.string().trim().max(1_000).optional(),
}).strict()
const imagePropsSchema = mediaItemSchema.extend({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
}).strict()
const galleryPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  images: z.array(mediaItemSchema).min(1).max(50),
  columns: z.number().int().min(1).max(6).optional(),
}).strict()
const carouselPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  images: z.array(mediaItemSchema).min(1).max(50),
  start_index: z.number().int().min(0).max(49).optional(),
}).strict()

const genericCardPropsSchema = z.object({
  title: z.string().trim().max(MAX_TITLE_CHARS).optional(),
  description: z.string().trim().max(MAX_TEXT_CHARS).optional(),
  requested_component: z.string().trim().max(120).optional(),
}).passthrough()

const componentNames = [
  'comparison-card',
  'bar-chart',
  'line-chart',
  'pie-chart',
  'metric-grid',
  'progress-list',
  'timeline',
  'key-value',
  'callout',
  'list',
  'calculation',
  'data-table',
  'details',
  'tabs',
  'accordion',
  'diff-view',
  'badge-group',
  'gauge',
  'series-chart',
  'scatter-chart',
  'heatmap',
  'calendar',
  'kanban',
  'button-group',
  'follow-up',
  'confirm',
  'approval',
  'form',
  'image',
  'gallery',
  'carousel',
  'generic-card',
] as const

const componentNameSchema = z.enum(componentNames)
export type InlineWidgetComponentName = z.infer<typeof componentNameSchema>
export type InlineWidgetTone = z.infer<typeof toneSchema>

const envelopeSchema = z.object({
  protocol: z.literal('kemo-ui').optional(),
  schema_version: z.union([z.literal(1), z.literal('1'), z.literal('1.0')]),
  surface: z.literal('inline').optional(),
  id: z.string().trim().regex(/^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/),
  component: componentNameSchema,
  props: z.unknown(),
  fallback: z.union([
    z.string().trim().min(1).max(4_000),
    z.object({ text: z.string().trim().min(1).max(4_000) }).strict(),
  ]).optional(),
  fallback_text: z.string().trim().min(1).max(4_000).optional(),
}).strict()

const propsSchemas = {
  'comparison-card': comparisonPropsSchema,
  'bar-chart': barChartPropsSchema,
  'line-chart': lineChartPropsSchema,
  'pie-chart': pieChartPropsSchema,
  'metric-grid': metricGridPropsSchema,
  'progress-list': progressListPropsSchema,
  timeline: timelinePropsSchema,
  'key-value': keyValuePropsSchema,
  callout: calloutPropsSchema,
  list: listPropsSchema,
  calculation: calculationPropsSchema,
  'data-table': dataTablePropsSchema,
  details: detailsPropsSchema,
  tabs: tabsPropsSchema,
  accordion: accordionPropsSchema,
  'diff-view': diffViewPropsSchema,
  'badge-group': badgeGroupPropsSchema,
  gauge: gaugePropsSchema,
  'series-chart': seriesChartPropsSchema,
  'scatter-chart': scatterChartPropsSchema,
  heatmap: heatmapPropsSchema,
  calendar: calendarPropsSchema,
  kanban: kanbanPropsSchema,
  'button-group': buttonGroupPropsSchema,
  'follow-up': followUpPropsSchema,
  confirm: confirmPropsSchema,
  approval: approvalPropsSchema,
  form: formPropsSchema,
  image: imagePropsSchema,
  gallery: galleryPropsSchema,
  carousel: carouselPropsSchema,
  'generic-card': genericCardPropsSchema,
} satisfies Record<InlineWidgetComponentName, z.ZodTypeAny>

type ComponentPropsMap = {
  'comparison-card': z.infer<typeof comparisonPropsSchema>
  'bar-chart': z.infer<typeof barChartPropsSchema>
  'line-chart': z.infer<typeof lineChartPropsSchema>
  'pie-chart': z.infer<typeof pieChartPropsSchema>
  'metric-grid': z.infer<typeof metricGridPropsSchema>
  'progress-list': z.infer<typeof progressListPropsSchema>
  timeline: z.infer<typeof timelinePropsSchema>
  'key-value': z.infer<typeof keyValuePropsSchema>
  callout: z.infer<typeof calloutPropsSchema>
  list: z.infer<typeof listPropsSchema>
  calculation: z.infer<typeof calculationPropsSchema>
  'data-table': z.infer<typeof dataTablePropsSchema>
  details: z.infer<typeof detailsPropsSchema>
  tabs: z.infer<typeof tabsPropsSchema>
  accordion: z.infer<typeof accordionPropsSchema>
  'diff-view': z.infer<typeof diffViewPropsSchema>
  'badge-group': z.infer<typeof badgeGroupPropsSchema>
  gauge: z.infer<typeof gaugePropsSchema>
  'series-chart': z.infer<typeof seriesChartPropsSchema>
  'scatter-chart': z.infer<typeof scatterChartPropsSchema>
  heatmap: z.infer<typeof heatmapPropsSchema>
  calendar: z.infer<typeof calendarPropsSchema>
  kanban: z.infer<typeof kanbanPropsSchema>
  'button-group': z.infer<typeof buttonGroupPropsSchema>
  'follow-up': z.infer<typeof followUpPropsSchema>
  confirm: z.infer<typeof confirmPropsSchema>
  approval: z.infer<typeof approvalPropsSchema>
  form: z.infer<typeof formPropsSchema>
  image: z.infer<typeof imagePropsSchema>
  gallery: z.infer<typeof galleryPropsSchema>
  carousel: z.infer<typeof carouselPropsSchema>
  'generic-card': z.infer<typeof genericCardPropsSchema>
}

export type InlineWidgetAction = z.infer<typeof widgetActionSchema>

export type InlineWidgetDefinition = {
  [Name in InlineWidgetComponentName]: {
    protocol: 'kemo-ui'
    schemaVersion: 1
    surface: 'inline'
    id: string
    component: Name
    props: ComponentPropsMap[Name]
    fallbackText: string
  }
}[InlineWidgetComponentName]

export type InlineWidgetParseResult =
  | { ok: true; widget: InlineWidgetDefinition }
  | { ok: false; message: string; fallbackText: string }

const legacyComponentNames: Record<string, InlineWidgetComponentName> = {
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
  if (component === 'line-chart' && normalizedName === 'area-chart') props.area ??= true
  if (component === 'pie-chart' && normalizedName.includes('donut')) props.donut ??= true
  if (component === 'series-chart' && normalizedName.includes('stacked')) props.stacked ??= true
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
