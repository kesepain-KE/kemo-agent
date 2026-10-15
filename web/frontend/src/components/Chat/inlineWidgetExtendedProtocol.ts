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

const uiNodeTypeAliases: Record<string, string> = {
  stack: 'col',
  vstack: 'col',
  'v-stack': 'col',
  vertical: 'col',
  column: 'col',
  hstack: 'row',
  'h-stack': 'row',
  horizontal: 'row',
  group: 'box',
  section: 'box',
  container: 'box',
  grid: 'box',
  form: 'box',
  'form-section': 'box',
  'field-group': 'box',
  input: 'text-input',
  'text-field': 'text-input',
  text_field: 'text-input',
  number: 'number-input',
  numeric: 'number-input',
  date: 'date-picker',
  range: 'slider',
  toggle: 'switch',
  dropdown: 'select',
  'multi-select': 'checkbox-group',
  choices: 'radio',
  choice: 'radio',
  heading: 'title',
  paragraph: 'text',
  body: 'text',
  small: 'caption',
  action: 'button',
  separator: 'divider',
  space: 'spacer',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeUiNode(value: unknown, index = 0, fieldMode = false): unknown {
  if (typeof value === 'string') return { type: 'text', text: value }
  if (!isRecord(value)) return value
  // Some model adapters wrap node attributes in `props`; flatten only that
  // declarative object, never execute or preserve arbitrary callbacks.
  const nestedProps = isRecord(value.props) ? value.props : {}
  const source = {
    ...nestedProps,
    ...value,
  }
  delete source.props
  const explicitType = String(source.type || source.kind || source.component || '').trim().toLowerCase()
  const nestedType = String(nestedProps.type || nestedProps.kind || nestedProps.component || '').trim().toLowerCase()
  const rawType = explicitType || nestedType
  const rawFieldType = String(
    source.input_type
      || source.inputType
      || source.control
      || source.control_type
      || source.field_type
      || source.fieldType
      || (['input', 'field', 'control'].includes(rawType) ? nestedType : ''),
  ).trim().toLowerCase()
  const fieldTypeAliases: Record<string, string> = {
    'text-input': 'text-input',
    text: 'text-input',
    string: 'text-input',
    textarea: 'textarea',
    'text-area': 'textarea',
    number: 'number-input',
    'number-input': 'number-input',
    numeric: 'number-input',
    date: 'date-picker',
    'date-picker': 'date-picker',
    range: 'slider',
    slider: 'slider',
    boolean: 'switch',
    checkbox: 'switch',
    toggle: 'switch',
    switch: 'switch',
    select: 'select',
    dropdown: 'select',
    radio: 'radio',
    multiselect: 'checkbox-group',
    'multi-select': 'checkbox-group',
    'checkbox-group': 'checkbox-group',
    choices: 'radio',
  }
  const inferredFieldType = Array.isArray(source.options) || Array.isArray(source.choices)
    ? 'select'
    : 'text-input'
  const type = ['input', 'field', 'control'].includes(rawType)
    ? fieldTypeAliases[rawFieldType] || inferredFieldType
    : fieldMode && fieldTypeAliases[rawType]
      ? fieldTypeAliases[rawType]
      : uiNodeTypeAliases[rawType] || rawType
  // A few model adapters omit `type` on a field node. Infer only the
  // declarative input shapes that this protocol already supports.
  const inferredType = type || (
    Array.isArray(source.options) || Array.isArray(source.choices)
      ? 'select'
      : (typeof source.name === 'string' || typeof source.id === 'string' || typeof source.field === 'string')
        ? 'text-input'
        : ''
  )
  if (inferredType) {
    source.type = inferredType
    delete source.kind
    delete source.component
  }

  if (['box', 'row', 'col', 'card', 'list-view', 'list-item', 'transition'].includes(inferredType)) {
    const children = source.children ?? source.nodes ?? source.items ?? source.content ?? source.fields
    if (Array.isArray(children)) {
      const childrenAreFields = Array.isArray(source.fields) || rawType === 'form' || rawType === 'form-section' || rawType === 'field-group'
      const normalizedChildren = children.map((child, childIndex) => normalizeUiNode(child, childIndex, childrenAreFields))
      if (rawType === 'form' && source.submit !== undefined) {
        const submit = isRecord(source.submit) ? source.submit : { label: String(source.submit) }
        normalizedChildren.push(normalizeUiNode({
          type: 'button',
          label: submit.label ?? source.submit_label ?? '提交',
          action: submit.action ?? source.action ?? { type: 'fill-input', text: submit.template ?? submit.text ?? submit.message ?? '提交' },
          text: submit.text,
          template: submit.template,
        }, normalizedChildren.length))
      }
      source.children = normalizedChildren
    } else if (typeof children === 'string' && children.trim()) {
      source.children = [{ type: 'text', text: children }]
    }
    delete source.nodes
    delete source.items
    delete source.content
    delete source.fields
    delete source.submit
    delete source.submit_label
    delete source.reset_label
    delete source.label
    delete source.action
  } else if (['title', 'text', 'caption', 'markdown'].includes(inferredType)) {
    if (typeof source.text !== 'string') {
      const content = source.content ?? source.value
      if (typeof content === 'string') source.text = content
    }
    delete source.content
    delete source.value
  } else if (inferredType === 'icon' && typeof source.name !== 'string' && typeof source.icon === 'string') {
    source.name = source.icon
    delete source.icon
  }
  if (['date-picker', 'text-input', 'textarea', 'number-input', 'slider', 'switch', 'select', 'radio', 'checkbox-group'].includes(inferredType)) {
    const label = source.label ?? source.title ?? source.placeholder ?? source.name ?? `字段 ${index + 1}`
    const name = source.name ?? source.id ?? source.field ?? source.key
    source.label = String(label)
    source.name = typeof name === 'string' && /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(name.trim())
      ? name.trim()
      : `field_${index + 1}`
    if (source.default_value === undefined && source.defaultValue !== undefined) source.default_value = source.defaultValue
    if (source.default_value === undefined && source.default !== undefined) source.default_value = source.default
    if (source.default_value === undefined && source.value !== undefined) source.default_value = source.value
    delete source.title
    delete source.id
    delete source.field
    delete source.key
    delete source.input_type
    delete source.inputType
    delete source.control
    delete source.control_type
    delete source.field_type
    delete source.fieldType
    delete source.defaultValue
    delete source.default
    delete source.value
    if (inferredType === 'select' || inferredType === 'radio' || inferredType === 'checkbox-group') {
      const options = source.options ?? source.choices
      if (Array.isArray(options)) {
        source.options = options.map((option) => isRecord(option)
          ? {
            label: String(option.label ?? option.name ?? option.value ?? ''),
            value: String(option.value ?? option.label ?? option.name ?? ''),
          }
          : { label: String(option), value: String(option) })
      }
      delete source.choices
    }
  }
  if (inferredType === 'button' && !isRecord(source.action)) {
    const actionType = typeof source.action === 'string' ? source.action : typeof source.action_type === 'string' ? source.action_type : 'fill-input'
    const actionText = source.text ?? source.message ?? source.template ?? source.value
    if (typeof actionText === 'string' && actionText.trim()) {
      source.action = { type: actionType, text: actionText }
    }
    delete source.action_type
    delete source.text
    delete source.message
    delete source.template
    delete source.value
  }
  if (inferredType === 'button' && isRecord(source.action)) {
    const action = source.action
    const actionType = typeof action.type === 'string' ? action.type : typeof action.action === 'string' ? action.action : 'fill-input'
    const actionText = action.text ?? action.message ?? action.template ?? action.value ?? source.text ?? source.message ?? source.template ?? source.value
    if (typeof actionText === 'string' && actionText.trim()) source.action = { type: actionType, text: actionText }
    delete source.text
    delete source.message
    delete source.template
    delete source.value
  }
  return source
}

function normalizeSubmitAction(value: unknown): unknown {
  if (!isRecord(value)) return value
  const submit = { ...value }
  if (isRecord(submit.action)) {
    const action = submit.action
    const actionType = typeof action.type === 'string'
      ? action.type
      : typeof action.action === 'string' ? action.action : ''
    const actionText = action.text ?? action.message ?? action.template ?? submit.template
    if (actionType && typeof actionText === 'string' && actionText.trim()) {
      submit.action = actionType
      if (typeof submit.template !== 'string') submit.template = actionText
    }
  }
  return submit
}

function normalizeExtendedAction(value: unknown, fallbackText = '操作'): unknown {
  if (typeof value === 'string') {
    return { type: 'fill-input', text: value }
  }
  if (!isRecord(value)) return value
  const type = value.type === 'fill-input' || value.type === 'send-message' || value.type === 'copy'
    ? value.type
    : value.action === 'fill-input' || value.action === 'send-message' || value.action === 'copy'
      ? value.action
      : 'fill-input'
  const text = value.text ?? value.message ?? value.template ?? value.value ?? fallbackText
  return { type, text: String(text) }
}

function normalizeExtendedActionButton(value: unknown, index: number): unknown {
  if (typeof value === 'string') {
    return { label: value, action: normalizeExtendedAction(value) }
  }
  if (!isRecord(value)) return value
  const actionSource = value.action ?? value
  const fallbackText = [value.text, value.message, value.template].find((item): item is string => typeof item === 'string') || '操作'
  const action = normalizeExtendedAction(actionSource, fallbackText)
  const actionText = isRecord(action) && typeof action.text === 'string' ? action.text : ''
  return {
    label: String(value.label ?? value.title ?? value.name ?? value.text ?? value.message ?? actionText ?? `操作 ${index + 1}`),
    action,
    ...(typeof value.tone === 'string' ? { tone: value.tone } : {}),
    ...(typeof value.disabled === 'boolean' ? { disabled: value.disabled } : {}),
  }
}

export function normalizeExtendedProps(component: ExtendedComponentName, props: Record<string, unknown>, requestedName: string): Record<string, unknown> {
  if (component === 'ui-card' && 'nodes' in props) {
    return { ...props, nodes: Array.isArray(props.nodes) ? props.nodes.map((node, index) => normalizeUiNode(node, index)) : props.nodes }
  }
  if (component === 'product-grid') {
    const products = Array.isArray(props.products)
      ? props.products.map((product) => {
        if (!isRecord(product)) return product
        const price = product.price ?? product.amount ?? product.value
        const previousPrice = product.previous_price ?? product.previousPrice ?? product.original_price
        return {
          ...product,
          ...(price !== undefined ? { price: String(price) } : {}),
          ...(previousPrice !== undefined ? { previous_price: String(previousPrice) } : {}),
        }
      })
      : props.products
    return { ...props, products }
  }
  if (component === 'order-summary' && Array.isArray(props.actions)) {
    return { ...props, actions: props.actions.map(normalizeExtendedActionButton) }
  }
  if ((component === 'poll' || component === 'rating') && isRecord(props.submit)) {
    const next: Record<string, unknown> = { ...props, submit: normalizeSubmitAction(props.submit) }
    if (component === 'poll' && Array.isArray(next.options)) {
      next.options = next.options.map((option) => isRecord(option)
        ? { ...option, label: String(option.label ?? option.title ?? option.value ?? ''), value: String(option.value ?? option.label ?? option.title ?? '') }
        : { label: String(option), value: String(option) })
    }
    return next
  }
  if (component === 'poll' && Array.isArray(props.options)) {
    return {
      ...props,
      options: props.options.map((option) => isRecord(option)
        ? { ...option, label: String(option.label ?? option.title ?? option.value ?? ''), value: String(option.value ?? option.label ?? option.title ?? '') }
        : { label: String(option), value: String(option) }),
    }
  }
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
