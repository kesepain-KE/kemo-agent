import { useState } from 'react'
import { t } from '../../i18n'
import { DocumentMarkdown } from '../DocumentMarkdown'
import styles from './AgentTaskDetail.module.css'

const fields: Record<string, string> = {
  summary: '摘要', answer: '回复内容', content: '回复内容', text: '回复内容',
  message: '回复内容', narrative: '回复内容', results: '执行结果', findings: '发现',
  verification: '验证情况', remaining: '遗留事项', issues: '问题',
}
const statuses: Record<string, string> = {
  completed: '已完成', partial: '部分完成', blocked: '受阻', failed: '失败',
}

function content(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  // Some assistants return each Markdown table row in a separate array item.
  // Joining preserves contiguous rows, code fences and nested Markdown.
  if (Array.isArray(value) && value.every(item => typeof item === 'string')) {
    return value.join(value.some(item => /^\s*(?:\||```)/m.test(item)) ? '\n' : '\n\n')
  }
  return undefined
}

function ResultValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const text = content(value)
  if (text !== undefined) return <DocumentMarkdown content={text} />
  if (depth < 4 && value && typeof value === 'object') {
    return <div className={styles.resultTree}>{Object.entries(value).map(([key, item]) =>
      <section key={key}><h5>{fields[key] ? t(fields[key]) : key}</h5>
        <ResultValue value={item} depth={depth + 1} />
      </section>)}</div>
  }
  return <pre className={styles.toolValue}>{JSON.stringify(value, null, 2)}</pre>
}

export function AgentTaskResult({ result }: { result: unknown }) {
  const [showRaw, setShowRaw] = useState(false)
  let value = result
  if (typeof value === 'string' && value.length < 200_000 && value.trim().startsWith('{')) {
    try { value = JSON.parse(value) } catch { /* A plain reply remains readable Markdown. */ }
  }
  const plain = content(value)
  const object = value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined
  const sections = object ? Object.entries(object).flatMap(([key, item]) => {
    if (['status', 'artifacts'].includes(key) || item === null
      || (Array.isArray(item) && !item.length) || item === '') return []
    return [{ key, title: fields[key] ? t(fields[key]) : key, value: item }]
  }) : []
  return <div className={styles.result}>
    <div className={styles.resultBody}>
    {plain !== undefined ? <DocumentMarkdown content={plain} /> : sections.length ? <>
      {typeof object?.status === 'string' && statuses[object.status] && <span className={styles.resultStatus}>{t(statuses[object.status])}</span>}
      {sections.map(section => <section key={section.key} className={styles.resultSection}>
        <h5>{section.title}</h5><ResultValue value={section.value} />
      </section>)}
    </> : <ResultValue value={value} />}
    </div>
    {sections.length > 0 && <details className={styles.rawResult} onToggle={event => setShowRaw(event.currentTarget.open)}><summary>{t('查看结构化数据')}</summary>
      {showRaw && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </details>}
  </div>
}
