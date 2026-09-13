import type { ImportantMemoryLifecycle } from '../types/api'
import styles from './MemoryPage.module.css'

export function importantMemoryStatusLabel(lifecycle?: ImportantMemoryLifecycle, failed = false) {
  if (failed || !lifecycle) return '状态未知'
  return { valid: '有效', invalid: '已失效', untracked: '未校验', empty: '暂无内容' }[lifecycle.status] ?? '状态未知'
}

export function ImportantMemoryStatus({ lifecycle, failed }: { lifecycle?: ImportantMemoryLifecycle; failed: boolean }) {
  const invalid = !failed && lifecycle?.status === 'invalid'
  const reason = failed ? '状态读取失败，请重新读取。' : lifecycle?.reason || '暂未取得生命周期状态。'
  const hint = failed || !lifecycle ? '' : invalid
    ? '后续对话暂停注入；旧内容保留，待后台更新。'
    : !lifecycle.prompt_eligible && lifecycle.status !== 'empty'
      ? '当前配置未启用该记忆注入。'
      : lifecycle.status === 'untracked' ? '按兼容规则保留，不代表来源已验证。' : ''
  return <div className={`${styles.lifecycleNotice} ${invalid ? styles.lifecycleInvalid : ''}`} role="status" aria-label="临时重要记忆生命周期">
    <strong>{importantMemoryStatusLabel(lifecycle, failed)}</strong>
    <span>{reason}{hint && <small>{hint}</small>}</span>
  </div>
}
