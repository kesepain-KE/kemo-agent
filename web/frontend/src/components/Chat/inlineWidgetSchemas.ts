/**
 * Kemo inline-widget schema contract.
 *
 * This module owns validation, limits, component names, and the public type map.
 * Parsing/fallback/fence handling stays in inlineWidgetProtocol.ts so schema
 * changes cannot accidentally alter message segmentation behaviour.
 */
import { z } from 'zod'
import {
  extendedComponentNames,
  extendedPropsSchemas,
  type ExtendedPropsMap,
} from './inlineWidgetExtendedProtocol'

export const MAX_WIDGET_SOURCE_CHARS = 262_144
export const MAX_WIDGETS_PER_MESSAGE = 24
export const MAX_TEXT_CHARS = 20_000
export const MAX_TITLE_CHARS = 500

export const toneSchema = z.enum(['neutral', 'brand', 'success', 'warning', 'danger'])
export const displayValueSchema = z.union([
  z.string().max(160),
  z.number().finite(),
])
export const shortTextSchema = z.string().trim().min(1).max(MAX_TITLE_CHARS)
export const bodyTextSchema = z.string().trim().min(1).max(MAX_TEXT_CHARS)

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

export const componentNames = [
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
  ...extendedComponentNames,
  'generic-card',
] as const

export const componentNameSchema = z.enum(componentNames)
export type InlineWidgetComponentName = z.infer<typeof componentNameSchema>
export type InlineWidgetTone = z.infer<typeof toneSchema>

export const envelopeSchema = z.object({
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

export const propsSchemas = {
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
  ...extendedPropsSchemas,
  'generic-card': genericCardPropsSchema,
} satisfies Record<InlineWidgetComponentName, z.ZodTypeAny>

export type ComponentPropsMap = {
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
  'ui-card': ExtendedPropsMap['ui-card']
  map: ExtendedPropsMap['map']
  'media-player': ExtendedPropsMap['media-player']
  'file-list': ExtendedPropsMap['file-list']
  'product-grid': ExtendedPropsMap['product-grid']
  'order-summary': ExtendedPropsMap['order-summary']
  poll: ExtendedPropsMap['poll']
  rating: ExtendedPropsMap['rating']
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

