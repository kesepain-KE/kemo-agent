import { t, useLanguage } from '../../i18n'
import styles from './AgentTaskDetail.module.css'

const metrics = [
  { key: 'input_tokens', aliases: ['input_tokens', 'prompt_tokens'], title: '输入 Token' },
  { key: 'output_tokens', aliases: ['output_tokens', 'completion_tokens'], title: '输出 Token' },
  { key: 'total_tokens', aliases: ['total_tokens'], title: '总 Token' },
  { key: 'cached_input_tokens', aliases: ['cached_input_tokens', 'cached_prompt_tokens'], title: '缓存命中 Token' },
  { key: 'cache_miss_tokens', aliases: ['cache_miss_tokens'], title: '缓存未命中 Token' },
  { key: 'cache_hit_rate', aliases: ['cache_hit_rate'], title: '缓存命中率' },
  { key: 'reasoning_tokens', aliases: ['reasoning_tokens'], title: '推理 Token' },
  { key: 'provider_request_count', aliases: ['provider_request_count'], title: '模型请求次数' },
] as const

export function AgentUsage({ usage }: { usage: Record<string, number> }) {
  const language = useLanguage()
  const rows = metrics.flatMap(metric => {
    const key = metric.aliases.find(alias => Number.isFinite(usage[alias]))
    if (!key) return []
    const value = usage[key]
    return [{
      key: metric.key, title: t(metric.title),
      value: metric.key === 'cache_hit_rate'
        ? new Intl.NumberFormat(language, { style: 'percent', maximumFractionDigits: 1 }).format(value)
        : value.toLocaleString(language),
    }]
  })
  if (!rows.length) return null
  return <section className={styles.usage} aria-label={t('Token 用量')}>
    <h4>{t('Token 用量')}</h4>
    <dl>{rows.map(row => <div key={row.key}><dt>{row.title}</dt><dd>{row.value}</dd></div>)}</dl>
  </section>
}
