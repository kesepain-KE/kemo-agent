import { History, RotateCcw } from 'lucide-react'
import type { PlanRevisionSnapshot, PlanRevisionSummary } from '../types/api'
import { formatDateTime, StatusChip } from './ModuleUi'
import styles from './PlanRevisionPanel.module.css'

export interface PlanRevisionPanelProps {
  open: boolean
  revisions: PlanRevisionSummary[]
  selectedRevision: number
  snapshot?: PlanRevisionSnapshot
  loading?: boolean
  error?: string
  feedback?: string
  rollbackPending?: boolean
  rollbackAllowed?: boolean
  onSelect: (revision: number) => void
  onRollback: (revision: number) => void
}

export function PlanRevisionPanel({
  open,
  revisions,
  selectedRevision,
  snapshot,
  loading = false,
  error = '',
  feedback = '',
  rollbackPending = false,
  rollbackAllowed = true,
  onSelect,
  onRollback,
}: PlanRevisionPanelProps) {
  if (!open) return null
  return <section className={styles.panel} aria-label="计划历史版本">
    <header><History size={16} /><div><strong>历史版本</strong><span>回滚会创建新版本，不改写已有历史</span></div></header>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {feedback && <p className={styles.feedback} role="status">{feedback}</p>}
    {loading && !revisions.length ? <p className={styles.empty}>正在读取修订历史…</p> : null}
    {!loading && !revisions.length && !error ? <p className={styles.empty}>暂无历史版本</p> : null}
    {revisions.length ? <div className={styles.layout}>
      <div className={styles.list} role="list" aria-label="计划修订列表">
        {revisions.map((revision) => <button
          key={revision.revision}
          type="button"
          role="listitem"
          className={revision.revision === selectedRevision ? styles.active : ''}
          onClick={() => onSelect(revision.revision)}
        ><b>v{revision.revision}</b><span>{revision.note || `revision ${revision.revision}`}</span><small>{formatDateTime(revision.created_at)}</small></button>)}
      </div>
      <div className={styles.preview}>
        {!snapshot || snapshot.revision !== selectedRevision ? <p className={styles.empty}>正在读取版本内容…</p> : <>
          <div className={styles.previewTitle}><div><strong>{snapshot.title}</strong><span>revision {snapshot.revision}</span></div><StatusChip status={snapshot.status} /></div>
          <p>{snapshot.description}</p>
          <dl className={styles.facts}><div><dt>来源</dt><dd>{snapshot.source}</dd></div><div><dt>对话空间</dt><dd>{snapshot.session_id}</dd></div><div><dt>当前步骤</dt><dd>{snapshot.current_step || '—'}</dd></div><div><dt>自动接受</dt><dd>{snapshot.auto_accept ? '是' : '否'}</dd></div></dl>
          <div className={styles.steps}>{snapshot.steps.map((step, index) => <article key={step.step_id}>
            <b>{index + 1}</b>
            <div><strong>{step.title}</strong><span>{step.step_id} · {step.tool_name || '由智能体判断工具'} · {step.critical ? '关键步骤' : '非关键步骤'}</span>{step.depends_on.length ? <small>依赖：{step.depends_on.join(', ')}</small> : null}{step.tool_arguments && Object.keys(step.tool_arguments).length ? <details><summary>工具参数</summary><pre>{JSON.stringify(step.tool_arguments, null, 2)}</pre></details> : null}</div>
            <StatusChip status={step.status} />
          </article>)}</div>
          <button type="button" className={styles.rollback} disabled={rollbackPending || !rollbackAllowed} onClick={() => onRollback(snapshot.revision)} title={rollbackAllowed ? '' : '只能在待处理、已暂停或失败状态回滚'}><RotateCcw size={14} />{rollbackPending ? '正在回滚…' : rollbackAllowed ? `回滚到 v${snapshot.revision}` : '当前状态不可回滚'}</button>
        </>}
      </div>
    </div> : null}
  </section>
}
