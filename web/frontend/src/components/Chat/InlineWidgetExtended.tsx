import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import {
  AlertTriangle,
  CalendarDays,
  Check,
  Clock3,
  Download,
  ExternalLink,
  FileText,
  Info,
  MapPin,
  Play,
  Search,
  ShoppingCart,
  Sparkles,
  Star,
  User,
} from 'lucide-react'

import type { InlineWidgetAction, InlineWidgetDefinition, InlineWidgetTone } from './inlineWidgetProtocol'
import type { UiNode } from './inlineWidgetExtendedProtocol'
import styles from './InlineWidgetExtended.module.css'

type UiValue = string | number | boolean | string[]
type UiFieldNode = UiNode & {
  name: string
  label: string
  required?: boolean
  placeholder?: string
  default_value?: string | number | boolean | string[]
  min?: number
  max?: number
  step?: number
  multiple?: boolean
  options?: Array<{ label: string; value: string }>
}

const uiFieldTypes = new Set<UiNode['type']>([
  'date-picker', 'text-input', 'textarea', 'number-input', 'slider', 'switch',
  'select', 'radio', 'checkbox-group',
])

function toneClass(tone?: InlineWidgetTone) {
  return styles[tone || 'neutral'] || styles.neutral
}

function Header({ title, description, badge }: { title?: string; description?: string; badge?: string }) {
  if (!title && !description && !badge) return null
  return <header className={styles.header}><div>{title ? <h3>{title}</h3> : null}{description ? <p>{description}</p> : null}</div>{badge ? <span>{badge}</span> : null}</header>
}

function applyValues(template: string, values: Record<string, UiValue>): string {
  return template.replace(/\{\{\s*([A-Za-z][A-Za-z0-9_.-]{0,63})\s*\}\}/g, (_match, name: string) => {
    const value = values[name]
    if (Array.isArray(value)) return value.join('、')
    if (value === true) return '是'
    if (value === false) return '否'
    return value === undefined || value === '' ? '—' : String(value)
  })
}

function nodeDefaults(nodes: UiNode[], output: Record<string, UiValue> = {}) {
  for (const node of nodes) {
    if ('children' in node) nodeDefaults(node.children, output)
    else if (uiFieldTypes.has(node.type)) {
      const field = node as UiFieldNode
      if (field.type === 'checkbox-group') output[field.name] = Array.isArray(field.default_value) ? field.default_value : []
      else if (field.type === 'switch') output[field.name] = typeof field.default_value === 'boolean' ? field.default_value : false
      else output[field.name] = field.default_value ?? ''
    }
  }
  return output
}

const iconMap = {
  info: Info,
  check: Check,
  alert: AlertTriangle,
  star: Star,
  calendar: CalendarDays,
  'map-pin': MapPin,
  file: FileText,
  'shopping-cart': ShoppingCart,
  play: Play,
  sparkles: Sparkles,
  user: User,
  clock: Clock3,
  search: Search,
  download: Download,
  'external-link': ExternalLink,
} as const

function UiNodeView({ node, values, setValue, onAction, namespace }: {
  node: UiNode
  values: Record<string, UiValue>
  setValue: (name: string, value: UiValue) => void
  onAction?: (action: InlineWidgetAction) => void
  namespace: string
}) {
  if ('children' in node) {
    const className = [styles.uiGroup, styles[node.type.replace('-', '')], styles[`gap_${node.gap || 'md'}`], styles[`align_${node.align || 'stretch'}`], toneClass(node.tone)].filter(Boolean).join(' ')
    const Tag = node.type === 'list-view' ? 'ul' : node.type === 'list-item' ? 'li' : node.type === 'card' ? 'article' : 'div'
    return <Tag className={className}>{node.children.map((child, index) => <UiNodeView key={`${child.type}-${index}`} node={child} values={values} setValue={setValue} onAction={onAction} namespace={namespace} />)}</Tag>
  }
  if (node.type === 'title') return <h4 className={`${styles.uiTitle} ${toneClass(node.tone)}`}>{node.text}</h4>
  if (node.type === 'text') return <p className={`${styles.uiText} ${toneClass(node.tone)}`}>{node.text}</p>
  if (node.type === 'caption') return <small className={`${styles.uiCaption} ${toneClass(node.tone)}`}>{node.text}</small>
  if (node.type === 'markdown') return <div className={`${styles.uiMarkdown} ${toneClass(node.tone)}`}><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={{ img: ({ src, alt }) => typeof src === 'string' && /^\/(?!\/)/.test(src) ? <img src={src} alt={alt || ''} loading="lazy" referrerPolicy="no-referrer" /> : <span>[外部图片已阻止]</span> }}>{node.text}</ReactMarkdown></div>
  if (node.type === 'badge') return <span className={`${styles.uiBadge} ${toneClass(node.tone)}`}>{node.label}{node.value ? <b>{node.value}</b> : null}</span>
  if (node.type === 'icon') {
    const Icon = iconMap[node.name]
    return <span className={`${styles.uiIcon} ${toneClass(node.tone)}`} aria-label={node.label || node.name}><Icon size={18} aria-hidden="true" />{node.label ? <span>{node.label}</span> : null}</span>
  }
  if (node.type === 'image') return <figure className={styles.uiImage}><img src={node.src} alt={node.alt} loading="lazy" referrerPolicy="no-referrer" />{node.caption ? <figcaption>{node.caption}</figcaption> : null}</figure>
  if (node.type === 'divider') return <div className={styles.uiDivider}>{node.label ? <span>{node.label}</span> : null}</div>
  if (node.type === 'spacer') return <span className={`${styles.uiSpacer} ${styles[`space_${node.size || 'md'}`]}`} aria-hidden="true" />
  if (node.type === 'button') return <button type="button" className={`${styles.actionButton} ${toneClass(node.tone)}`} disabled={node.disabled || !onAction} onClick={() => onAction?.({ ...node.action, text: applyValues(node.action.text, values) })}>{node.label}</button>

  if (!uiFieldTypes.has(node.type)) return null
  const field = node as UiFieldNode
  const value = values[field.name]
  const label = <span>{field.label}{field.required ? ' *' : ''}</span>
  if (field.type === 'textarea') return <label className={styles.field}>{label}<textarea value={String(value ?? '')} placeholder={field.placeholder} onChange={(event) => setValue(field.name, event.target.value)} /></label>
  if (field.type === 'text-input' || field.type === 'date-picker' || field.type === 'number-input') return <label className={styles.field}>{label}<input type={field.type === 'date-picker' ? 'date' : field.type === 'number-input' ? 'number' : 'text'} value={String(value ?? '')} placeholder={field.placeholder} min={field.min} max={field.max} step={field.step} onChange={(event) => setValue(field.name, field.type === 'number-input' && event.target.value !== '' ? Number(event.target.value) : event.target.value)} /></label>
  if (field.type === 'slider') return <label className={styles.field}>{label}<div className={styles.sliderRow}><input type="range" value={Number(value || field.min || 0)} min={field.min} max={field.max} step={field.step} onChange={(event) => setValue(field.name, Number(event.target.value))} /><output>{String(value || field.min || 0)}</output></div></label>
  if (field.type === 'switch') return <label className={styles.switchField}><input type="checkbox" checked={Boolean(value)} onChange={(event) => setValue(field.name, event.target.checked)} /><span>{field.label}</span></label>
  if (field.type === 'select') return <label className={styles.field}>{label}<select multiple={field.multiple} value={field.multiple ? (Array.isArray(value) ? value : []) : String(value ?? '')} onChange={(event) => setValue(field.name, field.multiple ? Array.from(event.target.selectedOptions, (option) => option.value) : event.target.value)}>{!field.multiple ? <option value="">请选择</option> : null}{field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
  if (field.type === 'radio') return <fieldset className={styles.choiceGroup}><legend>{field.label}{field.required ? ' *' : ''}</legend>{field.options?.map((option) => <label key={option.value}><input type="radio" name={`${namespace}-${field.name}`} value={option.value} checked={value === option.value} onChange={() => setValue(field.name, option.value)} />{option.label}</label>)}</fieldset>
  const selected = Array.isArray(value) ? value : []
  return <fieldset className={styles.choiceGroup}><legend>{field.label}{field.required ? ' *' : ''}</legend>{field.options?.map((option) => <label key={option.value}><input type="checkbox" value={option.value} checked={selected.includes(option.value)} onChange={(event) => setValue(field.name, event.target.checked ? [...selected, option.value] : selected.filter((item) => item !== option.value))} />{option.label}</label>)}</fieldset>
}

function UiCardWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'ui-card' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, nodes } = widget.props
  const initial = useMemo(() => nodeDefaults(nodes), [nodes])
  const [values, setValues] = useState<Record<string, UiValue>>(initial)
  return <section className={styles.widget} aria-label={title || '组合交互卡片'}><Header title={title} description={description} badge="UI" /><div className={styles.uiRoot}>{nodes.map((node, index) => <UiNodeView key={`${node.type}-${index}`} node={node} values={values} setValue={(name, value) => setValues((current) => ({ ...current, [name]: value }))} onAction={onAction} namespace={widget.id} />)}</div></section>
}

function MapWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'map' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, center_label: centerLabel, points } = widget.props
  const [selected, setSelected] = useState(0)
  const point = points[selected]
  return <section className={styles.widget} aria-label={title || '位置地图'}><Header title={title} description={description} badge={centerLabel || `${points.length} 个位置`} /><div className={styles.mapCanvas} role="img" aria-label="位置分布示意图">{points.map((item, index) => <button type="button" key={`${item.label}-${index}`} aria-label={`${item.label}：纬度 ${item.latitude}，经度 ${item.longitude}`} className={`${styles.mapPoint} ${toneClass(item.tone)} ${index === selected ? styles.selected : ''}`} style={{ left: `${((item.longitude + 180) / 360) * 100}%`, top: `${((90 - item.latitude) / 180) * 100}%` }} onClick={() => setSelected(index)}><MapPin size={18} aria-hidden="true" /></button>)}</div><div className={styles.mapDetail}><strong>{point.label}</strong><span>{point.latitude.toFixed(5)}, {point.longitude.toFixed(5)}</span>{point.detail ? <p>{point.detail}</p> : null}{point.action ? <button type="button" className={styles.actionButton} disabled={!onAction} onClick={() => onAction?.(point.action!)}>使用此位置</button> : null}</div></section>
}

function MediaPlayerWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'media-player' }> }) {
  const { title, description, media_type: type, src, poster, caption, transcript } = widget.props
  return <section className={styles.widget} aria-label={title || (type === 'audio' ? '音频播放器' : '视频播放器')}><Header title={title} description={description} badge={type === 'audio' ? '音频' : '视频'} />{type === 'audio' ? <audio className={styles.mediaPlayer} controls preload="metadata" src={src} /> : <video className={styles.mediaPlayer} controls preload="metadata" src={src} poster={poster} />}{caption ? <p className={styles.mediaCaption}>{caption}</p> : null}{transcript ? <details className={styles.transcript}><summary>查看文字稿</summary><p>{transcript}</p></details> : null}</section>
}

function FileListWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'file-list' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, files } = widget.props
  return <section className={styles.widget} aria-label={title || '文件列表'}><Header title={title} description={description} badge={`${files.length} 个文件`} /><div className={styles.fileList}>{files.map((file, index) => <article key={`${file.name}-${index}`}><FileText size={20} aria-hidden="true" /><div><strong>{file.name}</strong>{file.description ? <p>{file.description}</p> : null}<small>{[file.mime_type, file.size].filter(Boolean).join(' · ')}</small></div>{file.url ? <a href={file.url} target="_blank" rel="noreferrer"><ExternalLink size={15} />查看</a> : file.action ? <button type="button" disabled={!onAction} onClick={() => onAction?.(file.action!)}>操作</button> : null}</article>)}</div></section>
}

function ProductGridWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'product-grid' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, products, columns = 3 } = widget.props
  return <section className={styles.widget} aria-label={title || '商品列表'}><Header title={title} description={description} badge={`${products.length} 项`} /><div className={styles.productGrid} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>{products.map((product, index) => <article key={`${product.name}-${index}`}>{product.image ? <img src={product.image} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <div className={styles.productPlaceholder}><ShoppingCart aria-hidden="true" /></div>}<div className={styles.productBody}>{product.badge ? <span className={styles.productBadge}>{product.badge}</span> : null}<strong>{product.name}</strong>{product.description ? <p>{product.description}</p> : null}{product.rating !== undefined ? <span className={styles.productRating}><Star size={14} fill="currentColor" />{product.rating.toFixed(1)}</span> : null}<div className={styles.price}><b>{product.price}</b>{product.previous_price ? <del>{product.previous_price}</del> : null}</div>{product.action ? <button type="button" className={styles.actionButton} disabled={!onAction} onClick={() => onAction?.(product.action!)}>选择</button> : null}</div></article>)}</div></section>
}

function money(value: number, currency: string) {
  return `${currency}${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)}`
}

function OrderSummaryWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'order-summary' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, currency = '¥', items, adjustments = [], actions = [] } = widget.props
  const subtotal = items.reduce((sum, item) => sum + item.quantity * item.unit_price, 0)
  const computedTotal = subtotal + adjustments.reduce((sum, item) => sum + item.amount, 0)
  return <section className={styles.widget} aria-label={title || '订单摘要'}><Header title={title} description={description} badge={`${items.length} 项`} /><div className={styles.orderLines}>{items.map((item, index) => <div key={`${item.label}-${index}`}><span><strong>{item.label}</strong>{item.detail ? <small>{item.detail}</small> : null}</span><span>× {item.quantity}</span><b>{money(item.quantity * item.unit_price, currency)}</b></div>)}</div><div className={styles.orderTotals}><div><span>小计</span><b>{money(subtotal, currency)}</b></div>{adjustments.map((item, index) => <div key={`${item.label}-${index}`}><span>{item.label}</span><b>{money(item.amount, currency)}</b></div>)}<div className={styles.grandTotal}><span>合计</span><strong>{money(widget.props.total ?? computedTotal, currency)}</strong></div></div>{actions.length ? <div className={styles.actionRow}>{actions.map((button, index) => <button type="button" key={`${button.label}-${index}`} className={`${styles.actionButton} ${toneClass(button.tone)}`} disabled={button.disabled || !onAction} onClick={() => onAction?.(button.action)}>{button.label}</button>)}</div> : null}</section>
}

function PollWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'poll' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, options, multiple = false, submit } = widget.props
  const [selected, setSelected] = useState<string[]>([])
  const toggle = (value: string) => setSelected((current) => multiple ? current.includes(value) ? current.filter((item) => item !== value) : [...current, value] : [value])
  const totalVotes = options.reduce((sum, option) => sum + (option.votes || 0), 0)
  return <section className={styles.widget} aria-label={title}><Header title={title} description={description} badge={multiple ? '多选' : '单选'} /><div className={styles.pollList}>{options.map((option) => { const active = selected.includes(option.value); const percent = totalVotes ? Math.round(((option.votes || 0) / totalVotes) * 100) : 0; return <button type="button" aria-pressed={active} className={active ? styles.selected : ''} key={option.value} onClick={() => toggle(option.value)}><span className={styles.pollChoice} aria-hidden="true">{active ? '●' : '○'}</span><span><strong>{option.label}</strong>{option.description ? <small>{option.description}</small> : null}</span>{totalVotes ? <b>{percent}%</b> : null}</button> })}</div><button type="button" className={`${styles.actionButton} ${toneClass(submit.tone)}`} disabled={!onAction || !selected.length} onClick={() => onAction?.({ type: submit.action, text: applyValues(submit.template, { selection: selected }) })}>{submit.label}</button></section>
}

function RatingWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'rating' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, label, max = 5, default_value: defaultValue = 0, submit } = widget.props
  const [value, setValue] = useState(Math.min(defaultValue, max))
  return <section className={styles.widget} aria-label={title || label}><Header title={title} description={description} badge={`${value}/${max}`} /><p className={styles.ratingLabel}>{label}</p><div className={styles.ratingRow} role="radiogroup" aria-label={label}>{Array.from({ length: max }, (_, index) => index + 1).map((score) => <button type="button" role="radio" aria-checked={value === score} aria-label={`${score} 分`} key={score} className={score <= value ? styles.activeStar : ''} onClick={() => setValue(score)}><Star fill="currentColor" /></button>)}</div>{submit ? <button type="button" className={`${styles.actionButton} ${toneClass(submit.tone)}`} disabled={!onAction || value <= 0} onClick={() => onAction?.({ type: submit.action, text: applyValues(submit.template, { rating: value }) })}>{submit.label}</button> : null}</section>
}

export function ExtendedInlineWidget({ widget, onAction }: { widget: InlineWidgetDefinition; onAction?: (action: InlineWidgetAction) => void }) {
  switch (widget.component) {
    case 'ui-card': return <UiCardWidget widget={widget} onAction={onAction} />
    case 'map': return <MapWidget widget={widget} onAction={onAction} />
    case 'media-player': return <MediaPlayerWidget widget={widget} />
    case 'file-list': return <FileListWidget widget={widget} onAction={onAction} />
    case 'product-grid': return <ProductGridWidget widget={widget} onAction={onAction} />
    case 'order-summary': return <OrderSummaryWidget widget={widget} onAction={onAction} />
    case 'poll': return <PollWidget widget={widget} onAction={onAction} />
    case 'rating': return <RatingWidget widget={widget} onAction={onAction} />
    default: return null
  }
}
