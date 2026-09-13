import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { getRuntimeLogs } from '../api/client'
import { formatDateTime } from '../components/ModuleUi'
import type { RuntimeLogCategory } from '../types/api'
import styles from './RuntimeLogsPanel.module.css'

const categories: [RuntimeLogCategory, string][] = [
  ['all', '全部'], ['backend', '后端日志'], ['threads', '后端线程'], ['terminal', '终端日志'], ['message', '消息日志'],
]
const states: Record<string, string> = { success: '成功', completed: '完成', failed: '失败', error: '失败', cancelled: '已取消', running: '运行中', starting: '启动中', stopping: '停止中', stopped: '已停止', recorded: '已记录' }
const sources = { sqlite: '持久化记录', memory: '本进程内存', snapshot: '实时快照' }

export function RuntimeLogsPanel({ user }: { user: string }) {
  return <LogView key={user} user={user} />
}

function LogView({ user }: { user: string }) {
  const [category, setCategory] = useState<RuntimeLogCategory>('all')
  const [page, setPage] = useState(1)
  const forceRefresh = useRef(false)
  const query = useQuery({
    queryKey: ['runtime-logs', user, category, page],
    queryFn: () => {
      const refresh = forceRefresh.current
      forceRefresh.current = false
      return getRuntimeLogs(user, category, page, refresh)
    },
    enabled: Boolean(user), staleTime: 5_000, gcTime: 60_000,
    refetchInterval: 10_000, refetchIntervalInBackground: false,
  })
  const data = query.data
  return <section className={styles.panel} aria-label="执行记录日志">
    <header className={styles.header}>
      <div><h3>执行记录日志</h3><p>分类查看近期记录 · 前台每 10 秒更新</p></div>
      <button type="button" disabled={query.isFetching} onClick={() => { forceRefresh.current = true; void query.refetch() }}>
        <RefreshCw size={14} />刷新日志
      </button>
    </header>
    <div className={styles.tabs} role="tablist" aria-label="执行记录分类">
      {categories.map(([key, label]) => <button type="button" role="tab" key={key}
        aria-selected={category === key} aria-controls="runtime-log-records"
        onClick={() => { setCategory(key); setPage(1) }}>{label}</button>)}
    </div>
    <p className={styles.hint}>
      {category === 'threads' ? '当前后端进程线程快照，不是历史执行轨迹；不展示线程私有数据。'
        : category === 'terminal' ? '仅显示本进程观察到的 shell 调用状态、耗时及退出码；完整输出请查看对应工具卡片。后台作业结束需再次查询状态才能被观察到。'
          : category === 'message' ? '仅展示当前用户的消息收发元数据，不复制消息正文及附件路径。'
            : category === 'backend' ? '系统及当前用户的定时任务记录、工具调用和组件生命周期摘要。'
              : '全部为以下来源的近期合并视图，不代表完整历史。线程行是当前快照。'}
    </p>
    {query.isError ? <p role="alert" className={styles.error}>日志读取失败，请刷新重试。{data ? '下方为上次读取的记录。' : ''}</p> : null}
    {data?.source_errors.map((error) => <p role="alert" className={styles.error} key={error}>{error}</p>)}
    <div id="runtime-log-records" role="tabpanel" aria-label={categories.find(([key]) => key === category)?.[1]} aria-busy={query.isFetching} className={styles.body}>
      {query.isPending ? <p className={styles.empty}>正在读取日志…</p> : null}
      {data?.entries.map((entry) => <article key={entry.id} className={styles.row}>
        <div className={styles.identity}><strong>{entry.title}</strong><small>{categories.find(([key]) => key === entry.category)?.[1]} · {sources[entry.source]}</small></div>
        <time>{formatDateTime(entry.occurred_at)}</time>
        <span>{entry.duration_ms == null ? '—' : `${entry.duration_ms} ms`}{entry.exit_code != null ? ` · 退出码 ${entry.exit_code}` : ''}</span>
        <span className={entry.status === 'failed' || entry.status === 'error' ? styles.failed : styles.state}>{states[entry.status] || entry.status}</span>
        {entry.detail ? <p className={styles.detail}>{entry.detail}</p> : null}
      </article>)}
      {data && !data.entries.length ? <p className={styles.empty}>{data.source_errors.length ? '部分日志源不可用，暂时没有可显示的记录。' : '当前分类暂无记录。'}</p> : null}
    </div>
    <footer className={styles.footer}>
      <span>近期 {data?.pagination.total_items ?? 0} 条 · 第 {data?.pagination.page ?? page} / {data?.pagination.total_pages ?? 1} 页</span>
      <button type="button" disabled={!data?.pagination.has_previous || query.isFetching} onClick={() => setPage((data?.pagination.page ?? page) - 1)}>上一页</button>
      <button type="button" disabled={!data?.pagination.has_next || query.isFetching} onClick={() => setPage((data?.pagination.page ?? page) + 1)}>下一页</button>
    </footer>
    <p className={styles.hint}>持久化源各取最近 200 条，读取缓存 5 秒；内存摘要全进程最多 2048 条 / 1 小时，重启清空；线程快照最多 200 条。</p>
  </section>
}
