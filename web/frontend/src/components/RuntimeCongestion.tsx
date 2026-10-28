import { useQuery } from '@tanstack/react-query'
import { Activity, RefreshCw } from 'lucide-react'
import { getRuntimeStatus } from '../api/client'
import type { RuntimeStatusResponse } from '../types/api'
import { t, useLanguage } from '../i18n'
import styles from './RuntimeCongestion.module.css'

type Congestion = RuntimeStatusResponse['congestion']

/** Fast, bounded snapshots must not rebuild the prompt, token chart or page DOM. */
export function RuntimeCongestion({ user, sessionId, initial }: {
  user: string; sessionId: string; initial: Congestion
}) {
  useLanguage()
  const query = useQuery({
    queryKey: ['runtime-congestion', user, sessionId],
    queryFn: async ({ signal }) => (await getRuntimeStatus(user, sessionId, ['congestion'], signal)).congestion,
    initialData: initial,
    initialDataUpdatedAt: 0,
    enabled: Boolean(user),
    retry: false,
    gcTime: 0,
    refetchInterval: 1500,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  })
  const cg = query.data
  const pools = [
    { name: 'Provider 请求', used: cg.provider.active_requests, max: cg.provider.max_requests,
      available: cg.provider.available_requests, waiting: cg.provider.waiting_estimate },
    { name: 'Web 聊天槽位', used: cg.web.active_chats, max: cg.web.max_chats, waiting: cg.web.pending_chats },
    { name: '消息工作线程', used: cg.message_router.active_workers, max: cg.message_router.max_workers,
      waiting: cg.message_router.queued_messages },
  ]
  return <section className="card card-pad" aria-label={t('拥塞与并发')}>
    <div className={styles.heading}>
      <h3 className="section-title"><Activity size={16} aria-hidden="true" />{t('拥塞与并发')}</h3>
      <button className="btn btn-ghost btn-sm" aria-label={t('刷新并发状态')}
        disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={14} aria-hidden="true" /></button>
    </div>
    <div className={styles.grid}>
      {pools.map(pool => {
        const pct = pool.max > 0 ? pool.used / pool.max * 100 : 0
        return <div className={styles.pool} key={pool.name} role="group" aria-label={t(pool.name)}>
          <div className={styles.title}><strong>{t(pool.name)}</strong><span>{pool.used} / {pool.max}</span></div>
          <div className="progress-track" role="progressbar" aria-label={t(pool.name)}
            aria-valuemin={0} aria-valuemax={pool.max} aria-valuenow={pool.used}>
            <div className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, pct))}%`,
              background: pct > 80 ? 'var(--danger)' : pct > 55 ? 'var(--warning)' : 'var(--accent)' }} />
          </div>
          <div className={styles.foot}><span>{t('占用')} {pct.toFixed(0)}%</span>
            {pool.available != null && <span>{t('可用')} {pool.available}</span>}
            <span>{t('排队')} {pool.waiting}</span>
          </div>
        </div>
      })}
    </div>
    <p className={styles.hint}>{t('Provider 请求池仅统计模型请求，工具执行期间不占用。')}</p>
    {query.isError && <p className={styles.error} role="status">{t('刷新失败，当前显示上次成功读取的数据。')}</p>}
  </section>
}
