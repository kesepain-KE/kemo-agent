import { z } from 'zod'

const toneSchema = z.enum(['neutral', 'brand', 'success', 'warning', 'danger'])
const shortTextSchema = z.string().trim().min(1).max(500)
const bodyTextSchema = z.string().trim().min(1).max(20_000)
const siteUrlSchema = z.string().trim().regex(/^\/(?!\/)[^\s]{1,2000}$/, '只允许站内相对 URL')
const actionTypeSchema = z.enum(['fill-input', 'send-message', 'copy'])
const actionSchema = z.object({
  type: actionTypeSchema,
  text: z.string().min(1).max(20_000),
}).strict()

export const extendedComponentNames = [
  'ui-card',
  'map',
  'media-player',
  'file-list',
  'product-grid',
  'order-summary',
  'poll',
  'rating',
] as const

export type ExtendedComponentName = typeof extendedComponentNames[number]

export type UiNode =
  | { type: 'box' | 'row' | 'col' | 'card' | 'list-view' | 'list-item' | 'transition'; children: UiNode[]; gap?: 'none' | 'sm' | 'md' | 'lg'; align?: 'start' | 'center' | 'end' | 'stretch'; tone?: z.infer<typeof toneSchema> }
  | { type: 'title' | 'text' | 'caption' | 'markdown'; text: string; tone?: z.infer<typeof toneSchema> }
  | { type: 'badge'; label: string; value?: string; tone?: z.infer<typeof toneSchema> }
  | { type: 'icon'; name: 'info' | 'check' | 'alert' | 'star' | 'calendar' | 'map-pin' | 'file' | 'shopping-cart' | 'play' | 'sparkles' | 'user' | 'clock' | 'search' | 'download' | 'external-link'; label?: string; tone?: z.infer<typeof toneSchema> }
  | { type: 'image'; src: string; alt: string; caption?: string }
  | { type: 'divider'; label?: string }
  | { type: 'spacer'; size?: 'sm' | 'md' | 'lg' }
  | { type: 'button'; label: string; action: z.infer<typeof actionSchema>; tone?: z.infer<typeof toneSchema>; disabled?: boolean }
  | { type: 'date-picker' | 'text-input' | 'textarea' | 'number-input' | 'slider' | 'switch'; name: string; label: string; required?: boolean; placeholder?: string; default_value?: string | number | boolean; min?: number; max?: number; step?: number }
  | { type: 'select' | 'radio' | 'checkbox-group'; name: string; label: string; required?: boolean; multiple?: boolean; options: Array<{ label: string; value: string }>; default_value?: string | string[] }

const uiNodeSchema: z.ZodType<UiNode> = z.lazy(() => z.union([
  ...(['box', 'row', 'col', 'card', 'list-view', 'list-item', 'transition'] as const).map((type) => z.object({
    type: z.literal(type),
    children: z.array(uiNodeSchema).min(1).max(100),
    gap: z.enum(['none', 'sm', 'md', 'lg']).optional(),
    align: z.enum(['start', 'center', 'end', 'stretch']).optional(),
    tone: toneSchema.optional(),
  }).strict()),
  ...(['title', 'text', 'caption', 'markdown'] as const).map((type) => z.object({
    type: z.literal(type),
    text: bodyTextSchema,
    tone: toneSchema.optional(),
  }).strict()),
  z.object({
    type: z.literal('badge'),
    label: z.string().trim().min(1).max(120),
    value: z.string().trim().max(160).optional(),
    tone: toneSchema.optional(),
  }).strict(),
  z.object({
    type: z.literal('icon'),
    name: z.enum(['info', 'check', 'alert', 'star', 'calendar', 'map-pin', 'file', 'shopping-cart', 'play', 'sparkles', 'user', 'clock', 'search', 'download', 'external-link']),
    label: z.string().trim().max(120).optional(),
    tone: toneSchema.optional(),
  }).strict(),
  z.object({
    type: z.literal('image'),
    src: siteUrlSchema,
    alt: z.string().trim().min(1).max(500),
    caption: z.string().trim().max(1_000).optional(),
  }).strict(),
  z.object({ type: z.literal('divider'), label: z.string().trim().max(120).optional() }).strict(),
  z.object({ type: z.literal('spacer'), size: z.enum(['sm', 'md', 'lg']).optional() }).strict(),
  z.object({
    type: z.literal('button'),
    label: z.string().trim().min(1).max(80),
    action: actionSchema,
    tone: toneSchema.optional(),
    disabled: z.boolean().optional(),
  }).strict(),
  ...(['date-picker', 'text-input', 'textarea', 'number-input', 'slider', 'switch'] as const).map((type) => z.object({
    type: z.literal(type),
    name: z.string().trim().regex(/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/),
    label: z.string().trim().min(1).max(120),
    required: z.boolean().optional(),
    placeholder: z.string().max(240).optional(),
    default_value: z.union([z.string().max(2_000), z.number().finite(), z.boolean()]).optional(),
    min: z.number().finite().optional(),
    max: z.number().finite().optional(),
    step: z.number().finite().positive().optional(),
  }).strict()),
  ...(['select', 'radio', 'checkbox-group'] as const).map((type) => z.object({
    type: z.literal(type),
    name: z.string().trim().regex(/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/),
    label: z.string().trim().min(1).max(120),
    required: z.boolean().optional(),
    multiple: z.boolean().optional(),
    options: z.array(z.object({
      label: z.string().trim().min(1).max(120),
      value: z.string().max(240),
    }).strict()).min(1).max(100),
    default_value: z.union([z.string().max(2_000), z.array(z.string().max(240)).max(100)]).optional(),
  }).strict()),
] as unknown as [z.ZodTypeAny, z.ZodTypeAny, ...z.ZodTypeAny[]])) as z.ZodType<UiNode>

function uiTreeSize(nodes: UiNode[], depth = 1): { count: number; depth: number } {
  let count = 0
  let maximumDepth = depth
  for (const node of nodes) {
    count += 1
    if ('children' in node) {
      const child = uiTreeSize(node.children, depth + 1)
      count += child.count
      maximumDepth = Math.max(maximumDepth, child.depth)
    }
  }
  return { count, depth: maximumDepth }
}

const uiCardPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  nodes: z.array(uiNodeSchema).min(1).max(100),
}).strict().superRefine((value, context) => {
  const size = uiTreeSize(value.nodes)
  if (size.count > 300) context.addIssue({ code: z.ZodIssueCode.custom, path: ['nodes'], message: '组件节点总数不能超过 300' })
  if (size.depth > 8) context.addIssue({ code: z.ZodIssueCode.custom, path: ['nodes'], message: '组件嵌套不能超过 8 层' })
})

const mapPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  center_label: z.string().trim().max(160).optional(),
  points: z.array(z.object({
    label: z.string().trim().min(1).max(160),
    latitude: z.number().finite().min(-90).max(90),
    longitude: z.number().finite().min(-180).max(180),
    detail: z.string().trim().max(1_000).optional(),
    tone: toneSchema.optional(),
    action: actionSchema.optional(),
  }).strict()).min(1).max(200),
}).strict()

const mediaPlayerPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  media_type: z.enum(['audio', 'video']),
  src: siteUrlSchema,
  poster: siteUrlSchema.optional(),
  caption: z.string().trim().max(2_000).optional(),
  transcript: bodyTextSchema.optional(),
  autoplay: z.literal(false).optional(),
}).strict()

const fileListPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  files: z.array(z.object({
    name: z.string().trim().min(1).max(240),
    description: z.string().trim().max(1_000).optional(),
    size: z.string().trim().max(80).optional(),
    mime_type: z.string().trim().max(160).optional(),
    url: siteUrlSchema.optional(),
    action: actionSchema.optional(),
  }).strict()).min(1).max(200),
}).strict()

const productGridPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  products: z.array(z.object({
    name: z.string().trim().min(1).max(240),
    description: z.string().trim().max(2_000).optional(),
    image: siteUrlSchema.optional(),
    price: z.string().trim().min(1).max(120),
    previous_price: z.string().trim().max(120).optional(),
    badge: z.string().trim().max(80).optional(),
    rating: z.number().finite().min(0).max(5).optional(),
    action: actionSchema.optional(),
  }).strict()).min(1).max(100),
  columns: z.number().int().min(1).max(4).optional(),
}).strict()

const orderSummaryPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  currency: z.string().trim().min(1).max(20).optional(),
  items: z.array(z.object({
    label: z.string().trim().min(1).max(240),
    quantity: z.number().int().min(1).max(1_000),
    unit_price: z.number().finite().min(0).max(1_000_000_000),
    detail: z.string().trim().max(500).optional(),
  }).strict()).min(1).max(200),
  adjustments: z.array(z.object({
    label: z.string().trim().min(1).max(160),
    amount: z.number().finite().min(-1_000_000_000).max(1_000_000_000),
  }).strict()).max(50).optional(),
  total: z.number().finite().min(0).max(1_000_000_000).optional(),
  actions: z.array(z.object({
    label: z.string().trim().min(1).max(80),
    action: actionSchema,
    tone: toneSchema.optional(),
    disabled: z.boolean().optional(),
  }).strict()).max(12).optional(),
}).strict()

const pollPropsSchema = z.object({
  title: shortTextSchema,
  description: bodyTextSchema.optional(),
  options: z.array(z.object({
    label: z.string().trim().min(1).max(240),
    value: z.string().trim().min(1).max(240),
    description: z.string().trim().max(1_000).optional(),
    votes: z.number().int().min(0).optional(),
  }).strict()).min(2).max(50),
  multiple: z.boolean().optional(),
  submit: z.object({
    label: z.string().trim().min(1).max(80),
    action: actionTypeSchema,
    template: z.string().min(1).max(20_000),
    tone: toneSchema.optional(),
  }).strict(),
}).strict()

const ratingPropsSchema = z.object({
  title: shortTextSchema.optional(),
  description: bodyTextSchema.optional(),
  label: z.string().trim().min(1).max(240),
  max: z.number().int().min(2).max(10).optional(),
  default_value: z.number().int().min(0).max(10).optional(),
  submit: z.object({
    label: z.string().trim().min(1).max(80),
    action: actionTypeSchema,
    template: z.string().min(1).max(20_000),
    tone: toneSchema.optional(),
  }).strict().optional(),
}).strict().refine((value) => (value.default_value ?? 0) <= (value.max ?? 5), {
  path: ['default_value'],
  message: '默认评分不能超过最大评分',
})

export const extendedPropsSchemas = {
  'ui-card': uiCardPropsSchema,
  map: mapPropsSchema,
  'media-player': mediaPlayerPropsSchema,
  'file-list': fileListPropsSchema,
  'product-grid': productGridPropsSchema,
  'order-summary': orderSummaryPropsSchema,
  poll: pollPropsSchema,
  rating: ratingPropsSchema,
} satisfies Record<ExtendedComponentName, z.ZodTypeAny>

export type ExtendedPropsMap = {
  [Name in ExtendedComponentName]: z.infer<(typeof extendedPropsSchemas)[Name]>
}

export const extendedComponentAliases: Record<string, ExtendedComponentName> = {
  ui: 'ui-card',
  ui_card: 'ui-card',
  'ui-card': 'ui-card',
  layout: 'ui-card',
  list_view: 'ui-card',
  'list-view': 'ui-card',
  map: 'map',
  map_view: 'map',
  'map-view': 'map',
  audio: 'media-player',
  video: 'media-player',
  media: 'media-player',
  media_player: 'media-player',
  'media-player': 'media-player',
  files: 'file-list',
  attachments: 'file-list',
  file_list: 'file-list',
  'file-list': 'file-list',
  products: 'product-grid',
  shop: 'product-grid',
  product_grid: 'product-grid',
  'product-grid': 'product-grid',
  cart: 'order-summary',
  checkout: 'order-summary',
  order_summary: 'order-summary',
  'order-summary': 'order-summary',
  survey: 'poll',
  poll: 'poll',
  stars: 'rating',
  rating: 'rating',
}

export function normalizeExtendedProps(component: ExtendedComponentName, props: Record<string, unknown>, requestedName: string): Record<string, unknown> {
  if (component === 'media-player' && !('media_type' in props)) {
    if (requestedName.includes('audio')) return { ...props, media_type: 'audio' }
    if (requestedName.includes('video')) return { ...props, media_type: 'video' }
  }
  return props
}

function collectUiText(nodes: UiNode[], output: string[] = []): string[] {
  for (const node of nodes) {
    if ('children' in node) collectUiText(node.children, output)
    else if ('text' in node && typeof node.text === 'string') output.push(node.text)
    else if (node.type === 'badge') output.push([node.label, node.value].filter(Boolean).join('：'))
    else if (node.type === 'button') output.push(node.label)
    else if ('label' in node && typeof node.label === 'string') output.push(node.label)
  }
  return output
}

export function deriveExtendedFallback(component: ExtendedComponentName, props: ExtendedPropsMap[ExtendedComponentName]): string {
  if (component === 'ui-card') {
    const value = props as ExtendedPropsMap['ui-card']
    return [value.title, value.description, collectUiText(value.nodes).slice(0, 40).join('；')].filter(Boolean).join('。')
  }
  if (component === 'map') {
    const value = props as ExtendedPropsMap['map']
    return [value.title, value.points.map((point) => `${point.label}（${point.latitude}, ${point.longitude}）`).join('；')].filter(Boolean).join('。')
  }
  if (component === 'media-player') {
    const value = props as ExtendedPropsMap['media-player']
    return [value.title, value.caption, value.transcript].filter(Boolean).join('。')
  }
  if (component === 'file-list') {
    const value = props as ExtendedPropsMap['file-list']
    return [value.title, value.files.map((file) => file.name).join('；')].filter(Boolean).join('。')
  }
  if (component === 'product-grid') {
    const value = props as ExtendedPropsMap['product-grid']
    return [value.title, value.products.map((product) => `${product.name}：${product.price}`).join('；')].filter(Boolean).join('。')
  }
  if (component === 'order-summary') {
    const value = props as ExtendedPropsMap['order-summary']
    const currency = value.currency || '¥'
    const computed = value.items.reduce((sum, item) => sum + item.quantity * item.unit_price, 0)
      + (value.adjustments || []).reduce((sum, item) => sum + item.amount, 0)
    return [value.title, value.items.map((item) => `${item.label} × ${item.quantity}`).join('；'), `合计：${currency}${value.total ?? computed}`].filter(Boolean).join('。')
  }
  if (component === 'poll') {
    const value = props as ExtendedPropsMap['poll']
    return [value.title, value.options.map((option) => option.label).join('；')].filter(Boolean).join('。')
  }
  const value = props as ExtendedPropsMap['rating']
  return [value.title, value.label, `评分范围 1～${value.max || 5}`].filter(Boolean).join('。')
}
