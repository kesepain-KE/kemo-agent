import { useMemo, useState } from 'react'
import { ChevronDown, LoaderCircle, TriangleAlert } from 'lucide-react'
import {
  parseInlineWidget,
  type InlineWidgetAction,
  type InlineWidgetDefinition,
  type InlineWidgetTone,
} from './inlineWidgetProtocol'
import styles from './InlineWidget.module.css'

function toneClass(tone: InlineWidgetTone | undefined): string {
  return styles[tone || 'neutral'] || styles.neutral
}

function displayValue(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function WidgetHeader({ title, description, status }: {
  title?: string
  description?: string
  status?: { label: string; tone?: InlineWidgetTone }
}) {
  if (!title && !description && !status) return null
  return (
    <header className={styles.header}>
      <div className={styles.heading}>
        {title ? <h3>{title}</h3> : null}
        {description ? <p>{description}</p> : null}
      </div>
      {status ? <span className={`${styles.status} ${toneClass(status.tone)}`}>{status.label}</span> : null}
    </header>
  )
}

function ComparisonWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'comparison-card' }> }) {
  const { title, description, status, items, calculation, highlight } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '对比卡片'}>
      <WidgetHeader title={title} description={description} status={status} />
      <div className={styles.comparisonGrid}>
        {items.map((item, index) => (
          <article className={`${styles.comparisonItem} ${toneClass(item.tone)}`} key={`${item.label}-${index}`}>
            <span className={styles.itemLabel}>{item.label}</span>
            <strong>{displayValue(item.value)}</strong>
            {item.secondary ? <span className={styles.itemSecondary}>{item.secondary}</span> : null}
            {item.note ? <small>{item.note}</small> : null}
          </article>
        ))}
      </div>
      {calculation || highlight ? (
        <div className={styles.resultSection}>
          {calculation ? (
            <div className={styles.calculationSummary}>
              {calculation.label ? <span>{calculation.label}</span> : null}
              <code>{calculation.expression}</code>
              {calculation.note ? <small>{calculation.note}</small> : null}
            </div>
          ) : null}
          {highlight ? <strong className={`${styles.highlight} ${toneClass(highlight.tone)}`}>{highlight.text}</strong> : null}
        </div>
      ) : null}
    </section>
  )
}

function niceChartMaximum(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const normalized = value / power
  const rounded = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return rounded * power
}

function formatChartNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

function BarChartWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'bar-chart' }> }) {
  const { title, description, data, unit = '', value_prefix: prefix = '', value_suffix: suffix = '', max } = widget.props
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const chartMaximum = useMemo(() => Math.max(max || 0, niceChartMaximum(Math.max(...data.map((item) => item.value)))), [data, max])
  const ticks = useMemo(() => Array.from({ length: 5 }, (_, index) => chartMaximum * (4 - index) / 4), [chartMaximum])
  const minimumWidth = Math.max(440, data.length * 58)
  return (
    <section className={styles.widget} aria-label={title || '柱状图'}>
      <WidgetHeader title={title} description={description} />
      <div className={styles.chartScroll} tabIndex={0} aria-label={`${title || '柱状图'}，可横向滚动`}>
        <div className={styles.chart} style={{ minWidth: `${minimumWidth}px` }}>
          <div className={styles.chartTicks} aria-hidden="true">
            {ticks.map((tick) => <span key={tick}><i />{formatChartNumber(tick)}</span>)}
          </div>
          <div className={styles.chartBars} style={{ gridTemplateColumns: `repeat(${data.length}, minmax(42px, 1fr))` }}>
            {data.map((item, index) => {
              const percentage = Math.max(1.5, Math.min(100, item.value / chartMaximum * 100))
              const active = activeIndex === index
              const valueText = `${prefix}${formatChartNumber(item.value)}${suffix}${unit}`
              return (
                <div className={styles.chartItem} key={`${item.label}-${index}`}>
                  <div className={styles.barArea}>
                    {active ? (
                      <div
                        className={`${styles.tooltip} ${percentage >= 72 ? styles.tooltipInside : ''} ${index === 0 ? styles.tooltipFirst : ''} ${index === data.length - 1 ? styles.tooltipLast : ''}`}
                        style={percentage >= 72 ? { top: '8px' } : { bottom: `calc(${percentage}% + 8px)` }}
                        role="tooltip"
                      >
                        <strong>{item.label}</strong>
                        <span>{valueText}</span>
                        {item.detail ? <small>{item.detail}</small> : null}
                      </div>
                    ) : null}
                    <div
                      className={`${styles.bar} ${toneClass(item.tone)} ${item.highlight ? styles.barHighlight : ''}`}
                      style={{ height: `${percentage}%` }}
                      role="img"
                      tabIndex={0}
                      aria-label={`${item.label}：${valueText}${item.detail ? `，${item.detail}` : ''}`}
                      onPointerEnter={() => setActiveIndex(index)}
                      onPointerLeave={() => setActiveIndex((current) => current === index ? null : current)}
                      onFocus={() => setActiveIndex(index)}
                      onBlur={() => setActiveIndex((current) => current === index ? null : current)}
                    />
                  </div>
                  <span className={styles.chartLabel} title={item.label}>{item.label}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>
      <details className={styles.dataDetails}>
        <summary><ChevronDown size={14} aria-hidden="true" />查看图表数据</summary>
        <div className={styles.tableScroll}>
          <table>
            <thead><tr><th>项目</th><th>数值</th><th>说明</th></tr></thead>
            <tbody>
              {data.map((item, index) => (
                <tr key={`${item.label}-row-${index}`}>
                  <td>{item.label}</td>
                  <td>{prefix}{formatChartNumber(item.value)}{suffix}{unit}</td>
                  <td>{item.detail || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

function LineChartWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'line-chart' }> }) {
  const { title, description, data, unit = '', value_prefix: prefix = '', value_suffix: suffix = '', min, max, area } = widget.props
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const width = 720
  const height = 260
  const left = 48
  const right = 18
  const top = 18
  const bottom = 44
  const values = data.map((item) => item.value)
  const rawMin = min ?? Math.min(...values)
  const rawMax = max ?? Math.max(...values)
  const padding = rawMin === rawMax ? Math.max(1, Math.abs(rawMin) * .1) : (rawMax - rawMin) * .08
  const chartMin = min ?? rawMin - padding
  const chartMax = max ?? rawMax + padding
  const range = Math.max(Number.EPSILON, chartMax - chartMin)
  const points = data.map((item, index) => ({
    x: left + (data.length === 1 ? 0 : index / (data.length - 1)) * (width - left - right),
    y: top + (chartMax - item.value) / range * (height - top - bottom),
  }))
  const polyline = points.map((point) => `${point.x},${point.y}`).join(' ')
  const areaPoints = `${left},${height - bottom} ${polyline} ${width - right},${height - bottom}`
  const ticks = Array.from({ length: 5 }, (_, index) => chartMax - range * index / 4)
  const active = activeIndex === null ? null : data[activeIndex]
  return (
    <section className={styles.widget} aria-label={title || '折线图'}>
      <WidgetHeader title={title} description={description} />
      <div className={styles.lineChartWrap}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title || '折线图'}，共 ${data.length} 个数据点`}>
          {ticks.map((tick, index) => {
            const y = top + index / 4 * (height - top - bottom)
            return <g key={`${tick}-${index}`} aria-hidden="true"><line x1={left} y1={y} x2={width - right} y2={y} /><text x={left - 8} y={y + 4}>{formatChartNumber(tick)}</text></g>
          })}
          {area ? <polygon className={styles.lineArea} points={areaPoints} aria-hidden="true" /> : null}
          <polyline className={styles.linePath} points={polyline} aria-hidden="true" />
          {points.map((point, index) => {
            const item = data[index]
            const valueText = `${prefix}${formatChartNumber(item.value)}${suffix}${unit}`
            return (
              <g key={`${item.label}-${index}`}>
                <circle
                  className={`${styles.linePoint} ${item.highlight ? styles.linePointHighlight : ''}`}
                  cx={point.x}
                  cy={point.y}
                  r={item.highlight ? 6 : 4.5}
                  tabIndex={0}
                  role="img"
                  aria-label={`${item.label}：${valueText}${item.detail ? `，${item.detail}` : ''}`}
                  onPointerEnter={() => setActiveIndex(index)}
                  onPointerLeave={() => setActiveIndex((current) => current === index ? null : current)}
                  onFocus={() => setActiveIndex(index)}
                  onBlur={() => setActiveIndex((current) => current === index ? null : current)}
                />
                {(data.length <= 12 || index === 0 || index === data.length - 1) ? <text className={styles.lineLabel} x={point.x} y={height - 18} textAnchor="middle">{item.label.slice(0, 10)}</text> : null}
              </g>
            )
          })}
        </svg>
        {active ? <div className={styles.chartInlineTooltip} role="tooltip"><strong>{active.label}</strong><span>{prefix}{formatChartNumber(active.value)}{suffix}{unit}</span>{active.detail ? <small>{active.detail}</small> : null}</div> : null}
      </div>
      <ChartDataDetails data={data} value={(item) => `${prefix}${formatChartNumber(item.value)}${suffix}${unit}`} />
    </section>
  )
}

const piePalette = ['#665cff', '#4ca7e8', '#1eaf7d', '#c98c25', '#d55b65', '#9b72e8', '#4eb8a5', '#ee8b60', '#7886a4', '#b7a13d']

function PieChartWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'pie-chart' }> }) {
  const { title, description, data, unit = '', donut = true } = widget.props
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const total = data.reduce((sum, item) => sum + item.value, 0)
  let consumed = 0
  const stops = data.map((item, index) => {
    const start = total > 0 ? consumed / total * 100 : index / data.length * 100
    consumed += item.value
    const end = total > 0 ? consumed / total * 100 : (index + 1) / data.length * 100
    return `${piePalette[index % piePalette.length]} ${start}% ${end}%`
  }).join(', ')
  const active = activeIndex === null ? null : data[activeIndex]
  return (
    <section className={styles.widget} aria-label={title || '饼图'}>
      <WidgetHeader title={title} description={description} />
      <div className={styles.pieLayout}>
        <div className={styles.pieStage}>
          <div className={`${styles.pie} ${donut ? styles.donut : ''}`} style={{ background: `conic-gradient(${stops})` }} role="img" aria-label={`${title || '饼图'}，总计 ${formatChartNumber(total)}${unit}`} />
          <div className={styles.pieCenter}><strong>{active ? formatChartNumber(active.value) : formatChartNumber(total)}</strong><span>{active?.label || unit || '合计'}</span></div>
        </div>
        <div className={styles.pieLegend}>
          {data.map((item, index) => {
            const percent = total > 0 ? item.value / total * 100 : 0
            return (
              <div
                key={`${item.label}-${index}`}
                tabIndex={0}
                onPointerEnter={() => setActiveIndex(index)}
                onPointerLeave={() => setActiveIndex((current) => current === index ? null : current)}
                onFocus={() => setActiveIndex(index)}
                onBlur={() => setActiveIndex((current) => current === index ? null : current)}
                aria-label={`${item.label}：${formatChartNumber(item.value)}${unit}，${formatChartNumber(percent)}%`}
              >
                <i style={{ background: piePalette[index % piePalette.length] }} />
                <span>{item.label}</span><strong>{formatChartNumber(item.value)}{unit}</strong><small>{formatChartNumber(percent)}%</small>
              </div>
            )
          })}
        </div>
      </div>
      <ChartDataDetails data={data} value={(item) => `${formatChartNumber(item.value)}${unit}`} />
    </section>
  )
}

function ChartDataDetails({ data, value }: {
  data: Array<{ label: string; value: number; detail?: string }>
  value: (item: { label: string; value: number; detail?: string }) => string
}) {
  return (
    <details className={styles.dataDetails}>
      <summary><ChevronDown size={14} aria-hidden="true" />查看图表数据</summary>
      <div className={styles.tableScroll}>
        <table>
          <thead><tr><th>项目</th><th>数值</th><th>说明</th></tr></thead>
          <tbody>{data.map((item, index) => <tr key={`${item.label}-${index}`}><td>{item.label}</td><td>{value(item)}</td><td>{item.detail || '—'}</td></tr>)}</tbody>
        </table>
      </div>
    </details>
  )
}

function MetricGridWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'metric-grid' }> }) {
  const { title, description, items } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '指标摘要'}>
      <WidgetHeader title={title} description={description} />
      <div className={styles.metricGrid}>
        {items.map((item, index) => (
          <article className={`${styles.metric} ${toneClass(item.tone)}`} key={`${item.label}-${index}`}>
            <span>{item.label}</span>
            <strong>{displayValue(item.value)}</strong>
            <div>{item.secondary ? <small>{item.secondary}</small> : null}{item.delta ? <b>{item.delta}</b> : null}</div>
          </article>
        ))}
      </div>
    </section>
  )
}

function ProgressListWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'progress-list' }> }) {
  const { title, description, items } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '进度列表'}>
      <WidgetHeader title={title} description={description} />
      <div className={styles.progressList}>
        {items.map((item, index) => {
          const maximum = item.max || 100
          const percentage = Math.max(0, Math.min(100, item.value / maximum * 100))
          return (
            <div className={styles.progressItem} key={`${item.label}-${index}`}>
              <div><strong>{item.label}</strong><span>{formatChartNumber(item.value)} / {formatChartNumber(maximum)}</span></div>
              <div className={styles.progressTrack} role="progressbar" aria-label={item.label} aria-valuemin={0} aria-valuemax={maximum} aria-valuenow={item.value}>
                <i className={toneClass(item.tone)} style={{ width: `${percentage}%` }} />
              </div>
              {item.detail ? <small>{item.detail}</small> : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function TimelineWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'timeline' }> }) {
  const { title, description, items } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '时间线'}>
      <WidgetHeader title={title} description={description} />
      <ol className={styles.timeline}>
        {items.map((item, index) => (
          <li key={`${item.title}-${index}`} className={toneClass(item.tone)}>
            <i aria-hidden="true" />
            <div>
              <header><strong>{item.title}</strong>{item.status ? <b>{item.status}</b> : null}</header>
              {item.time ? <time>{item.time}</time> : null}
              {item.description ? <p>{item.description}</p> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function KeyValueWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'key-value' }> }) {
  const { title, description, items } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '键值详情'}>
      <WidgetHeader title={title} description={description} />
      <dl className={styles.keyValueGrid}>
        {items.map((item, index) => (
          <div key={`${item.label}-${index}`} className={toneClass(item.tone)}>
            <dt>{item.label}</dt><dd>{displayValue(item.value)}</dd>{item.detail ? <small>{item.detail}</small> : null}
          </div>
        ))}
      </dl>
    </section>
  )
}

function CalloutWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'callout' }> }) {
  const { title, content, tone } = widget.props
  return (
    <aside className={`${styles.widget} ${styles.callout} ${toneClass(tone)}`} aria-label={title || '提示'}>
      {title ? <strong>{title}</strong> : null}<p>{content}</p>
    </aside>
  )
}

function ListWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'list' }> }) {
  const { title, description, ordered, items } = widget.props
  const Tag = ordered ? 'ol' : 'ul'
  return (
    <section className={styles.widget} aria-label={title || '列表'}>
      <WidgetHeader title={title} description={description} />
      <Tag className={styles.richList}>
        {items.map((item, index) => typeof item === 'string'
          ? <li key={`${item}-${index}`}>{item}</li>
          : <li key={`${item.title}-${index}`} className={toneClass(item.tone)}><strong>{item.title}</strong>{item.description ? <p>{item.description}</p> : null}</li>)}
      </Tag>
    </section>
  )
}

function CalculationWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'calculation' }> }) {
  const { title, description, steps, result } = widget.props
  return (
    <section className={styles.widget} aria-label={title || '计算过程'}>
      <WidgetHeader title={title} description={description} />
      <ol className={styles.calculationSteps}>
        {steps.map((step, index) => (
          <li key={`${step.expression}-${index}`}>
            <span>{step.label || `步骤 ${index + 1}`}</span>
            <code>{step.expression}</code>
            {step.result ? <strong>{step.result}</strong> : null}
          </li>
        ))}
      </ol>
      {result ? (
        <div className={`${styles.calculationResult} ${toneClass(result.tone)}`}>
          <span>{result.label || '结果'}</span><strong>{displayValue(result.value)}</strong>
        </div>
      ) : null}
    </section>
  )
}

function DataTableWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'data-table' }> }) {
  const { title, description, caption, columns, rows, searchable, page_size: pageSize = 20 } = widget.props
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(null)
  const [page, setPage] = useState(0)
  const [visibleKeys, setVisibleKeys] = useState(() => new Set(columns.filter((column) => !column.hidden).map((column) => column.key)))
  const visibleColumns = columns.filter((column) => visibleKeys.has(column.key))
  const filteredRows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    const filtered = needle
      ? rows.filter((row) => columns.some((column) => displayValue(row[column.key]).toLocaleLowerCase().includes(needle)))
      : rows
    if (!sort) return filtered
    return [...filtered].sort((left, right) => {
      const a = left[sort.key]
      const b = right[sort.key]
      const order = typeof a === 'number' && typeof b === 'number'
        ? a - b
        : displayValue(a).localeCompare(displayValue(b), 'zh-CN', { numeric: true })
      return sort.direction === 'asc' ? order : -order
    })
  }, [columns, query, rows, sort])
  const pageCount = Math.max(1, Math.ceil(filteredRows.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const displayedRows = filteredRows.slice(safePage * pageSize, (safePage + 1) * pageSize)
  const toggleSort = (key: string) => {
    setPage(0)
    setSort((current) => current?.key === key
      ? current.direction === 'asc' ? { key, direction: 'desc' } : null
      : { key, direction: 'asc' })
  }
  return (
    <section className={styles.widget} aria-label={title || caption || '数据表格'}>
      <WidgetHeader title={title} description={description} />
      {(searchable || rows.length > pageSize || columns.length > 4) ? (
        <div className={styles.tableTools}>
          {searchable ? (
            <label className={styles.tableSearch}>
              <span>筛选</span>
              <input value={query} onChange={(event) => { setQuery(event.target.value); setPage(0) }} placeholder="搜索当前表格" />
            </label>
          ) : <span />}
          {columns.length > 4 ? (
            <details className={styles.columnPicker}>
              <summary>显示列（{visibleColumns.length}/{columns.length}）</summary>
              <div>{columns.map((column) => <label key={column.key}><input type="checkbox" checked={visibleKeys.has(column.key)} onChange={() => setVisibleKeys((current) => {
                const next = new Set(current)
                if (next.has(column.key) && next.size > 1) next.delete(column.key)
                else next.add(column.key)
                return next
              })} />{column.label}</label>)}</div>
            </details>
          ) : null}
        </div>
      ) : null}
      <div className={styles.tableScroll}>
        <table>
          {caption ? <caption>{caption}</caption> : null}
          <thead><tr>{visibleColumns.map((column) => <th key={column.key} style={{ textAlign: column.align || 'left' }}>
            {column.sortable === false ? column.label : <button type="button" className={styles.sortButton} onClick={() => toggleSort(column.key)}>{column.label}{sort?.key === column.key ? sort.direction === 'asc' ? ' ↑' : ' ↓' : ''}</button>}
          </th>)}</tr></thead>
          <tbody>
            {displayedRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {visibleColumns.map((column) => <td key={column.key} style={{ textAlign: column.align || 'left' }}>{displayValue(row[column.key])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.tablePager}>
        <span>共 {filteredRows.length} 行 · 第 {safePage + 1}/{pageCount} 页</span>
        <div><button type="button" disabled={safePage <= 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</button><button type="button" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>下一页</button></div>
      </div>
    </section>
  )
}

function DetailsWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'details' }> }) {
  const { title, description, summary, sections, default_open: defaultOpen } = widget.props
  const [open, setOpen] = useState(Boolean(defaultOpen))
  return (
    <section className={styles.widget} aria-label={title || summary}>
      <WidgetHeader title={title} description={description} />
      <details className={styles.contentDetails} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary><ChevronDown size={15} aria-hidden="true" />{summary}</summary>
        <div className={styles.detailSections}>
          {sections.map((section, index) => (
            <section key={`${section.title || 'section'}-${index}`}>
              {section.title ? <h4>{section.title}</h4> : null}
              <p>{section.content}</p>
            </section>
          ))}
        </div>
      </details>
    </section>
  )
}

function TabsWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'tabs' }> }) {
  const { title, description, tabs, default_index: defaultIndex = 0 } = widget.props
  const [activeIndex, setActiveIndex] = useState(Math.min(defaultIndex, tabs.length - 1))
  const active = tabs[activeIndex]
  return <section className={styles.widget} aria-label={title || '标签页'}>
    <WidgetHeader title={title} description={description} />
    <div className={styles.tabList} role="tablist">{tabs.map((tab, index) => <button key={`${tab.label}-${index}`} type="button" role="tab" aria-selected={index === activeIndex} onClick={() => setActiveIndex(index)}>{tab.label}{tab.badge ? <span>{tab.badge}</span> : null}</button>)}</div>
    <div className={styles.tabPanel} role="tabpanel"><p>{active.content}</p></div>
  </section>
}

function AccordionWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'accordion' }> }) {
  const { title, description, items, allow_multiple: allowMultiple = true } = widget.props
  const [openItems, setOpenItems] = useState(() => new Set(items.flatMap((item, index) => item.default_open ? [index] : [])))
  return <section className={styles.widget} aria-label={title || '折叠面板'}>
    <WidgetHeader title={title} description={description} />
    <div className={styles.accordionList}>{items.map((item, index) => {
      const open = openItems.has(index)
      return <section key={`${item.title}-${index}`} className={`${styles.accordionItem} ${toneClass(item.tone)}`}>
        <button type="button" aria-expanded={open} onClick={() => setOpenItems((current) => {
          const next = allowMultiple ? new Set(current) : new Set<number>()
          if (current.has(index)) next.delete(index); else next.add(index)
          return next
        })}>{item.title}<ChevronDown size={15} aria-hidden="true" /></button>
        {open ? <p>{item.content}</p> : null}
      </section>
    })}</div>
  </section>
}

function DiffViewWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'diff-view' }> }) {
  const { title, description, filename, language, before, after } = widget.props
  const beforeLines = before.split('\n')
  const afterLines = after.split('\n')
  const length = Math.max(beforeLines.length, afterLines.length)
  return <section className={styles.widget} aria-label={title || filename || '差异视图'}>
    <WidgetHeader title={title || filename} description={description} status={language ? { label: language, tone: 'neutral' } : undefined} />
    <div className={styles.diffGrid}>
      <div><strong>修改前</strong>{Array.from({ length }, (_, index) => <code className={beforeLines[index] === afterLines[index] ? '' : styles.diffRemoved} key={index}><i>{index + 1}</i>{beforeLines[index] ?? ''}</code>)}</div>
      <div><strong>修改后</strong>{Array.from({ length }, (_, index) => <code className={beforeLines[index] === afterLines[index] ? '' : styles.diffAdded} key={index}><i>{index + 1}</i>{afterLines[index] ?? ''}</code>)}</div>
    </div>
  </section>
}

function BadgeGroupWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'badge-group' }> }) {
  const { title, description, items } = widget.props
  return <section className={styles.widget} aria-label={title || '标签组'}><WidgetHeader title={title} description={description} /><div className={styles.badgeGroup}>{items.map((item, index) => <span className={toneClass(item.tone)} key={`${item.label}-${index}`}><b>{item.label}</b>{item.value ? <small>{item.value}</small> : null}</span>)}</div></section>
}

function GaugeWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'gauge' }> }) {
  const { title, description, label, value, min = 0, max = 100, unit = '', tone } = widget.props
  const percentage = Math.max(0, Math.min(100, (value - min) / (max - min) * 100))
  return <section className={styles.widget} aria-label={title || label || '仪表盘'}><WidgetHeader title={title} description={description} /><div className={styles.gaugeLayout}>
    <div className={`${styles.gauge} ${toneClass(tone)}`} style={{ background: `conic-gradient(currentColor 0 ${percentage * .75}%, var(--surface-3) ${percentage * .75}% 75%, transparent 75%)` }} role="meter" aria-label={label || title || '当前值'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}><div><strong>{formatChartNumber(value)}{unit}</strong><span>{label || '当前值'}</span></div></div>
    <div className={styles.gaugeScale}><span>{formatChartNumber(min)}{unit}</span><b>{formatChartNumber(percentage)}%</b><span>{formatChartNumber(max)}{unit}</span></div>
  </div></section>
}

function SeriesChartWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'series-chart' }> }) {
  const { title, description, chart_type: chartType = 'bar', stacked = false, unit = '', categories, series } = widget.props
  const width = 760; const height = 310; const left = 52; const right = 18; const top = 18; const bottom = 52
  const allValues = series.flatMap((item) => item.data)
  const min = chartType === 'line' ? Math.min(...allValues) : 0
  const max = chartType === 'bar' && stacked
    ? Math.max(...categories.map((_, index) => series.reduce((sum, item) => sum + item.data[index], 0)))
    : Math.max(...allValues)
  const padding = min === max ? Math.max(1, Math.abs(min) * .1) : (max - min) * .08
  const chartMin = chartType === 'line' ? min - padding : 0
  const chartMax = Math.max(chartMin + Number.EPSILON, max + padding)
  const range = chartMax - chartMin
  return <section className={styles.widget} aria-label={title || '多系列图表'}><WidgetHeader title={title} description={description} /><div className={styles.seriesLegend}>{series.map((item, index) => <span className={toneClass(item.tone)} key={`${item.name}-${index}`}><i style={{ background: piePalette[index % piePalette.length] }} />{item.name}</span>)}</div>{chartType === 'line' ? <div className={styles.multiLineWrap}><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title || '多系列折线图'}，${series.length} 个系列`}>
    {Array.from({ length: 5 }, (_, index) => { const y = top + index / 4 * (height - top - bottom); const value = chartMax - range * index / 4; return <g key={index}><line x1={left} y1={y} x2={width - right} y2={y}/><text x={left - 8} y={y + 4}>{formatChartNumber(value)}</text></g> })}
    {series.map((item, seriesIndex) => { const points = item.data.map((value, index) => ({ x: left + (categories.length === 1 ? 0 : index / (categories.length - 1)) * (width - left - right), y: top + (chartMax - value) / range * (height - top - bottom) })); return <g key={item.name}><polyline points={points.map((point) => `${point.x},${point.y}`).join(' ')} style={{ stroke: piePalette[seriesIndex % piePalette.length] }} />{points.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="4" style={{ stroke: piePalette[seriesIndex % piePalette.length] }}><title>{item.name} · {categories[index]}：{formatChartNumber(item.data[index])}{unit}</title></circle>)}</g> })}
    {categories.map((category, index) => (categories.length <= 12 || index === 0 || index === categories.length - 1) ? <text key={category} x={left + (categories.length === 1 ? 0 : index / (categories.length - 1)) * (width - left - right)} y={height - 18} textAnchor="middle">{category.slice(0, 10)}</text> : null)}
  </svg></div> : <div className={styles.multiBarScroll}><div className={styles.multiBarChart} style={{ minWidth: `${Math.max(460, categories.length * Math.max(72, series.length * 22))}px`, gridTemplateColumns: `repeat(${categories.length}, minmax(62px, 1fr))` }}>{categories.map((category, categoryIndex) => <div className={styles.multiBarCategory} key={`${category}-${categoryIndex}`}><div className={stacked ? styles.stackedBars : styles.groupedBars}>{series.map((item, seriesIndex) => <i key={item.name} title={`${item.name} · ${category}：${formatChartNumber(item.data[categoryIndex])}${unit}`} style={{ height: `${Math.max(1, item.data[categoryIndex] / chartMax * 100)}%`, background: piePalette[seriesIndex % piePalette.length] }} />)}</div><span>{category}</span></div>)}</div></div>}
    <details className={styles.dataDetails}><summary><ChevronDown size={14} aria-hidden="true" />查看图表数据</summary><div className={styles.tableScroll}><table><thead><tr><th>项目</th>{series.map((item) => <th key={item.name}>{item.name}</th>)}</tr></thead><tbody>{categories.map((category, index) => <tr key={category}><td>{category}</td>{series.map((item) => <td key={item.name}>{formatChartNumber(item.data[index])}{unit}</td>)}</tr>)}</tbody></table></div></details>
  </section>
}

function ScatterChartWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'scatter-chart' }> }) {
  const { title, description, x_label: xLabel = 'X', y_label: yLabel = 'Y', data } = widget.props
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const xs = data.map((item) => item.x); const ys = data.map((item) => item.y)
  const xMin = Math.min(...xs); const xMax = Math.max(...xs); const yMin = Math.min(...ys); const yMax = Math.max(...ys)
  const xRange = Math.max(Number.EPSILON, xMax - xMin); const yRange = Math.max(Number.EPSILON, yMax - yMin)
  const active = activeIndex === null ? null : data[activeIndex]
  return <section className={styles.widget} aria-label={title || '散点图'}><WidgetHeader title={title} description={description} /><div className={styles.scatterWrap}>
    <svg viewBox="0 0 720 320" role="img" aria-label={`${title || '散点图'}，共 ${data.length} 个点`}><line x1="55" y1="270" x2="700" y2="270"/><line x1="55" y1="20" x2="55" y2="270"/><text x="680" y="300">{xLabel}</text><text x="10" y="22">{yLabel}</text>{data.map((item, index) => <circle key={`${item.label}-${index}`} className={toneClass(item.tone)} cx={55 + (item.x - xMin) / xRange * 645} cy={270 - (item.y - yMin) / yRange * 250} r="6" tabIndex={0} onPointerEnter={() => setActiveIndex(index)} onPointerLeave={() => setActiveIndex(null)} onFocus={() => setActiveIndex(index)} onBlur={() => setActiveIndex(null)} aria-label={`${item.label}：${xLabel} ${item.x}，${yLabel} ${item.y}`} />)}</svg>
    {active ? <div className={styles.chartInlineTooltip}><strong>{active.label}</strong><span>{xLabel} {formatChartNumber(active.x)} · {yLabel} {formatChartNumber(active.y)}</span>{active.detail ? <small>{active.detail}</small> : null}</div> : null}
  </div></section>
}

function HeatmapWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'heatmap' }> }) {
  const { title, description, x_labels: xLabels, y_labels: yLabels, cells, min, max, unit = '' } = widget.props
  const values = cells.map((cell) => cell.value); const low = min ?? Math.min(...values); const high = max ?? Math.max(...values); const range = Math.max(Number.EPSILON, high - low)
  const cellMap = new Map(cells.map((cell) => [`${cell.x}:${cell.y}`, cell]))
  return <section className={styles.widget} aria-label={title || '热力图'}><WidgetHeader title={title} description={description} /><div className={styles.heatmapScroll}><div className={styles.heatmap} style={{ gridTemplateColumns: `minmax(6rem, auto) repeat(${xLabels.length}, minmax(42px, 1fr))` }}><span />{xLabels.map((label) => <b key={label}>{label}</b>)}{yLabels.flatMap((rowLabel, y) => [<b key={`row-${rowLabel}`}>{rowLabel}</b>, ...xLabels.map((_, x) => {
    const cell = cellMap.get(`${x}:${y}`); const ratio = cell ? Math.max(0, Math.min(1, (cell.value - low) / range)) : 0
    return <span key={`${x}:${y}`} title={cell ? `${cell.label || `${rowLabel} / ${xLabels[x]}`}：${formatChartNumber(cell.value)}${unit}` : '无数据'} style={{ background: cell ? `color-mix(in srgb, var(--brand) ${Math.round(18 + ratio * 72)}%, var(--surface-2))` : 'var(--surface-2)' }}>{cell ? formatChartNumber(cell.value) : '—'}</span>
  })])}</div></div></section>
}

function CalendarWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'calendar' }> }) {
  const { title, description, month, items } = widget.props
  return <section className={styles.widget} aria-label={title || month || '日历'}><WidgetHeader title={title || month} description={description} /><div className={styles.calendarList}>{items.map((item, index) => <article className={toneClass(item.tone)} key={`${item.date}-${item.title}-${index}`}><time>{item.date}</time><div><strong>{item.title}</strong>{item.description ? <p>{item.description}</p> : null}</div></article>)}</div></section>
}

function KanbanWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'kanban' }> }) {
  const { title, description, columns } = widget.props
  return <section className={styles.widget} aria-label={title || '看板'}><WidgetHeader title={title} description={description} /><div className={styles.kanban}>{columns.map((column, index) => <section className={toneClass(column.tone)} key={`${column.title}-${index}`}><header><strong>{column.title}</strong><span>{column.items.length}</span></header><div>{column.items.map((item, itemIndex) => <article className={toneClass(item.tone)} key={`${item.title}-${itemIndex}`}><strong>{item.title}</strong>{item.description ? <p>{item.description}</p> : null}{item.meta ? <small>{item.meta}</small> : null}</article>)}</div></section>)}</div></section>
}

function ActionButton({ button, onAction }: { button: { label: string; action: InlineWidgetAction; tone?: InlineWidgetTone; disabled?: boolean }; onAction?: (action: InlineWidgetAction) => void }) {
  return <button type="button" className={`${styles.actionButton} ${toneClass(button.tone)}`} disabled={button.disabled || !onAction} onClick={() => onAction?.(button.action)}>{button.label}</button>
}

function ButtonGroupWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'button-group' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, buttons } = widget.props
  return <section className={styles.widget} aria-label={title || '操作按钮'}><WidgetHeader title={title} description={description} /><div className={styles.actionRow}>{buttons.map((button, index) => <ActionButton key={`${button.label}-${index}`} button={button} onAction={onAction} />)}</div></section>
}

function FollowUpWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'follow-up' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, prompts } = widget.props
  return <section className={styles.widget} aria-label={title || '建议追问'}><WidgetHeader title={title || '你可以继续问'} description={description} /><div className={styles.followUpList}>{prompts.map((prompt, index) => <button type="button" disabled={!onAction} key={`${prompt.label}-${index}`} onClick={() => onAction?.({ type: prompt.send ? 'send-message' : 'fill-input', text: prompt.text })}><strong>{prompt.label}</strong><span>{prompt.text}</span></button>)}</div></section>
}

function ConfirmWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'confirm' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, message, confirm, cancel_label: cancelLabel = '取消' } = widget.props
  const [dismissed, setDismissed] = useState(false)
  return <section className={styles.widget} aria-label={title || '确认操作'}><WidgetHeader title={title} description={description} /><p className={styles.actionMessage}>{dismissed ? '已取消此操作。' : message}</p>{!dismissed ? <div className={styles.actionRow}><ActionButton button={confirm} onAction={onAction} /><button type="button" className={styles.actionButton} onClick={() => setDismissed(true)}>{cancelLabel}</button></div> : null}</section>
}

function ApprovalWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'approval' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, request, approve, reject } = widget.props
  return <section className={styles.widget} aria-label={title || '审批请求'}><WidgetHeader title={title} description={description} status={{ label: '等待用户决定', tone: 'warning' }} /><p className={styles.actionMessage}>{request}</p><div className={styles.actionRow}><ActionButton button={approve} onAction={onAction} />{reject ? <ActionButton button={reject} onAction={onAction} /> : null}</div></section>
}

function templateFormValue(template: string, values: Record<string, string | number | boolean>): string {
  return template.replace(/\{\{\s*([A-Za-z][A-Za-z0-9_.-]{0,63})\s*\}\}/g, (_match, name: string) => displayValue(values[name]))
}

function FormWidget({ widget, onAction }: { widget: Extract<InlineWidgetDefinition, { component: 'form' }>; onAction?: (action: InlineWidgetAction) => void }) {
  const { title, description, fields, submit, reset_label: resetLabel } = widget.props
  const defaults = () => Object.fromEntries(fields.map((field) => [field.name, field.default_value ?? (field.type === 'checkbox' ? false : '')])) as Record<string, string | number | boolean>
  const [values, setValues] = useState(defaults)
  const [error, setError] = useState('')
  const update = (name: string, value: string | number | boolean) => setValues((current) => ({ ...current, [name]: value }))
  const submitForm = () => {
    const missing = fields.find((field) => field.required && (values[field.name] === '' || values[field.name] === false))
    if (missing) { setError(`请填写：${missing.label}`); return }
    setError('')
    onAction?.({ type: submit.action, text: templateFormValue(submit.template, values) })
  }
  return <section className={styles.widget} aria-label={title || '信息表单'}><WidgetHeader title={title} description={description} /><div className={styles.formGrid}>{fields.map((field) => <label key={field.name}><span>{field.label}{field.required ? ' *' : ''}</span>{field.type === 'textarea' ? <textarea value={String(values[field.name] ?? '')} placeholder={field.placeholder} onChange={(event) => update(field.name, event.target.value)} /> : field.type === 'select' ? <select value={String(values[field.name] ?? '')} onChange={(event) => update(field.name, event.target.value)}><option value="">请选择</option>{field.options?.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select> : field.type === 'radio' ? <div className={styles.radioGroup}>{field.options?.map((option) => <label key={option.value}><input type="radio" name={`${widget.id}-${field.name}`} checked={values[field.name] === option.value} onChange={() => update(field.name, option.value)} />{option.label}</label>)}</div> : field.type === 'checkbox' ? <input type="checkbox" checked={Boolean(values[field.name])} onChange={(event) => update(field.name, event.target.checked)} /> : <input type={field.type} value={String(values[field.name] ?? '')} placeholder={field.placeholder} onChange={(event) => update(field.name, field.type === 'number' ? event.target.value === '' ? '' : Number(event.target.value) : event.target.value)} />}</label>)}</div>{error ? <p className={styles.formError}>{error}</p> : null}<div className={styles.actionRow}><button type="button" className={`${styles.actionButton} ${toneClass(submit.tone)}`} disabled={!onAction} onClick={submitForm}>{submit.label}</button>{resetLabel ? <button type="button" className={styles.actionButton} onClick={() => { setValues(defaults()); setError('') }}>{resetLabel}</button> : null}</div></section>
}

function ImageWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'image' }> }) {
  const { title, description, src, alt, caption } = widget.props
  return <figure className={`${styles.widget} ${styles.mediaFigure}`}><WidgetHeader title={title} description={description} /><img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" />{caption ? <figcaption>{caption}</figcaption> : null}</figure>
}

function GalleryWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'gallery' }> }) {
  const { title, description, images, columns = 3 } = widget.props
  return <section className={styles.widget} aria-label={title || '图片画廊'}><WidgetHeader title={title} description={description} /><div className={styles.gallery} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>{images.map((item, index) => <figure key={`${item.src}-${index}`}><img src={item.src} alt={item.alt} loading="lazy" referrerPolicy="no-referrer" />{item.caption ? <figcaption>{item.caption}</figcaption> : null}</figure>)}</div></section>
}

function CarouselWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'carousel' }> }) {
  const { title, description, images, start_index: startIndex = 0 } = widget.props
  const [index, setIndex] = useState(Math.min(startIndex, images.length - 1)); const item = images[index]
  return <section className={styles.widget} aria-label={title || '图片轮播'}><WidgetHeader title={title} description={description} /><figure className={styles.carousel}><img src={item.src} alt={item.alt} loading="lazy" referrerPolicy="no-referrer" />{item.caption ? <figcaption>{item.caption}</figcaption> : null}<div><button type="button" onClick={() => setIndex((value) => (value - 1 + images.length) % images.length)}>上一张</button><span>{index + 1} / {images.length}</span><button type="button" onClick={() => setIndex((value) => (value + 1) % images.length)}>下一张</button></div></figure></section>
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function GenericValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span>—</span>
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return <span>{displayValue(value)}</span>
  if (depth >= 4) return <pre>{JSON.stringify(value, null, 2)}</pre>
  if (Array.isArray(value)) {
    const rows = value.filter(isPlainRecord)
    if (rows.length === value.length && rows.length > 0) {
      const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 20)
      return (
        <div className={styles.tableScroll}>
          <table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 500).map((row, rowIndex) => <tr key={rowIndex}>{columns.map((column) => <td key={column}><GenericValue value={row[column]} depth={depth + 1} /></td>)}</tr>)}</tbody>
          </table>
        </div>
      )
    }
    return <ul className={styles.genericList}>{value.slice(0, 500).map((item, index) => <li key={index}><GenericValue value={item} depth={depth + 1} /></li>)}</ul>
  }
  if (isPlainRecord(value)) {
    return <dl className={styles.genericObject}>{Object.entries(value).slice(0, 200).map(([key, item]) => <div key={key}><dt>{key}</dt><dd><GenericValue value={item} depth={depth + 1} /></dd></div>)}</dl>
  }
  return <span>{String(value)}</span>
}

function GenericCardWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'generic-card' }> }) {
  const { title, description, requested_component: requestedComponent, ...content } = widget.props
  return (
    <section className={styles.widget} aria-label={title || requestedComponent || '通用组件'}>
      <WidgetHeader title={title || requestedComponent || '通用组件'} description={description} status={requestedComponent ? { label: requestedComponent, tone: 'brand' } : undefined} />
      <div className={styles.genericContent}><GenericValue value={content} /></div>
    </section>
  )
}

function RenderedInlineWidget({ widget, onAction }: { widget: InlineWidgetDefinition; onAction?: (action: InlineWidgetAction) => void }) {
  switch (widget.component) {
    case 'comparison-card': return <ComparisonWidget widget={widget} />
    case 'bar-chart': return <BarChartWidget widget={widget} />
    case 'line-chart': return <LineChartWidget widget={widget} />
    case 'pie-chart': return <PieChartWidget widget={widget} />
    case 'metric-grid': return <MetricGridWidget widget={widget} />
    case 'progress-list': return <ProgressListWidget widget={widget} />
    case 'timeline': return <TimelineWidget widget={widget} />
    case 'key-value': return <KeyValueWidget widget={widget} />
    case 'callout': return <CalloutWidget widget={widget} />
    case 'list': return <ListWidget widget={widget} />
    case 'calculation': return <CalculationWidget widget={widget} />
    case 'data-table': return <DataTableWidget widget={widget} />
    case 'details': return <DetailsWidget widget={widget} />
    case 'tabs': return <TabsWidget widget={widget} />
    case 'accordion': return <AccordionWidget widget={widget} />
    case 'diff-view': return <DiffViewWidget widget={widget} />
    case 'badge-group': return <BadgeGroupWidget widget={widget} />
    case 'gauge': return <GaugeWidget widget={widget} />
    case 'series-chart': return <SeriesChartWidget widget={widget} />
    case 'scatter-chart': return <ScatterChartWidget widget={widget} />
    case 'heatmap': return <HeatmapWidget widget={widget} />
    case 'calendar': return <CalendarWidget widget={widget} />
    case 'kanban': return <KanbanWidget widget={widget} />
    case 'button-group': return <ButtonGroupWidget widget={widget} onAction={onAction} />
    case 'follow-up': return <FollowUpWidget widget={widget} onAction={onAction} />
    case 'confirm': return <ConfirmWidget widget={widget} onAction={onAction} />
    case 'approval': return <ApprovalWidget widget={widget} onAction={onAction} />
    case 'form': return <FormWidget widget={widget} onAction={onAction} />
    case 'image': return <ImageWidget widget={widget} />
    case 'gallery': return <GalleryWidget widget={widget} />
    case 'carousel': return <CarouselWidget widget={widget} />
    case 'generic-card': return <GenericCardWidget widget={widget} />
  }
}

export function InlineWidgetBlock({ source, blocked = false, onAction }: { source: string; blocked?: boolean; onAction?: (action: InlineWidgetAction) => void }) {
  const parsed = useMemo(() => blocked ? null : parseInlineWidget(source), [blocked, source])
  if (blocked) {
    return (
      <aside className={`${styles.feedback} ${styles.invalid}`} role="note">
        <TriangleAlert size={18} aria-hidden="true" />
        <div>
          <strong>组件数量超过限制</strong>
          <span>单条回复最多渲染 24 个可视化组件。</span>
        </div>
      </aside>
    )
  }
  if (!parsed) return null
  if (parsed.ok) return <RenderedInlineWidget widget={parsed.widget} onAction={onAction} />
  return (
    <aside className={`${styles.feedback} ${styles.invalid}`} role="note">
      <TriangleAlert size={18} aria-hidden="true" />
      <div>
        <strong>组件无法预览</strong>
        <span>{parsed.message}</span>
        {parsed.fallbackText ? <p>{parsed.fallbackText}</p> : null}
      </div>
    </aside>
  )
}

export function InlineWidgetPending({ interrupted = false }: { interrupted?: boolean }) {
  return (
    <aside className={`${styles.feedback} ${interrupted ? styles.invalid : styles.pending}`} role="status" aria-live="polite">
      {interrupted ? <TriangleAlert size={18} aria-hidden="true" /> : <LoaderCircle className={styles.spinner} size={18} aria-hidden="true" />}
      <div>
        <strong>{interrupted ? '组件未生成完整' : '正在生成可视化组件…'}</strong>
        <span>{interrupted ? '本次尝试在组件声明闭合前结束。' : '组件完成后会直接显示在正文中。'}</span>
      </div>
    </aside>
  )
}
