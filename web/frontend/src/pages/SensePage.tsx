import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  Database,
  Eye,
  FileText,
  FolderOpen,
  MoreVertical,
  Plus,
  RefreshCw,
  Settings2,
  Sparkles,
  UserRound,
} from 'lucide-react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { getSense } from '../api/client'
import { GlobalSenseStatus, type SenseStatus } from '../components/GlobalSenseStatus'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatDateTime, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import styles from './SensePage.module.css'

const statusLabels: Record<string, string> = {
  active: '已启用',
  filtered: '已过滤',
  invalid: '配置异常',
}

function senseTimestamp(recentUpdate: string, updatedAt: number) {
  if (recentUpdate) {
    const parsed = Date.parse(recentUpdate.replace(' ', 'T'))
    if (!Number.isNaN(parsed)) return parsed
  }
  return (updatedAt || 0) * 1000
}

export function SensePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const [guideOpen, setGuideOpen] = useState(false)
  const [previewSource, setPreviewSource] = useState<string | null>(null)
  const sourceSectionRef = useRef<HTMLElement>(null)
  const injectionSectionRef = useRef<HTMLElement>(null)
  const query = useQuery({ queryKey: ['sense', user], queryFn: () => getSense(user), enabled: Boolean(user) })
  const data = query.data
  const sources = data?.sources || []
  const registeredData = data?.summary.registered_data ?? data?.core_files ?? 0
  const injectedData = data?.summary.injected_data ?? sources.reduce((total, source) => total + (source.injected_items || 0), 0)
  const injection = data?.injection
  const estimatedTokens = injection?.estimated_tokens || 0
  const senseStatus: SenseStatus = query.isError
    ? 'error'
    : !data
      ? query.isFetching ? 'running' : 'idle'
      : !data.registry_available
        ? 'warning'
        : data.summary.registered > 0 && !data.injection_enabled
          ? 'warning'
          : data.summary.registered > 0
            ? 'success'
            : 'idle'

  const recentSources = useMemo(
    () => [...sources].sort((left, right) => senseTimestamp(right.recent_update, right.updated_at) - senseTimestamp(left.recent_update, left.updated_at)),
    [sources],
  )

  const goToSettings = () => navigate(`/settings?user=${encodeURIComponent(user)}`)
  const scrollMetricIntoView = (type: 'sources' | 'enabled' | 'data' | 'tokens') => {
    const target = type === 'tokens' ? injectionSectionRef.current : sourceSectionRef.current
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <ModuleFrame
      kicker="System Capability / Global Sense"
      title="全局感知"
      description="管理外部感知源、注册可注入数据，并展示用户过滤后进入系统提示词的真实结果。"
      actions={<>
        <button className="module-btn" onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCw className={query.isFetching ? styles.spinning : ''} size={15} />刷新数据</button>
        <button className="module-btn primary" onClick={() => setGuideOpen((value) => !value)}><Plus size={15} />注册感知源</button>
      </>}
    >
      {query.isError && <ModuleError message="全局感知状态读取失败，请检查注册模块后重试。" />}

      {guideOpen && <section className={styles.registrationGuide} aria-label="感知源注册说明">
        <span><FolderOpen size={18} /></span>
        <div><strong>每个模块使用一份声明和一个标准数据文件</strong><p>在 global_sense 下创建独立模块目录，sense.json 的 data_md 明确指定唯一可注入的 Markdown；其他文档和采集脚本不会被扫描。</p></div>
        <code>global_sense/&lt;module&gt;/sense.json + data_md</code>
      </section>}

      <GlobalSenseStatus
        sourceCount={data?.summary.registered || 0}
        enabledSourceCount={data?.summary.enabled || 0}
        registeredDataCount={registeredData}
        registeredPassedCount={registeredData}
        filteredSourceCount={data?.summary.enabled || 0}
        injectedTokens={estimatedTokens}
        status={senseStatus}
        refreshing={query.isFetching}
        onMetricClick={scrollMetricIntoView}
      />

      <div className={styles.workspace}>
        <div className={styles.leftColumn}>
          <section className={styles.panel} ref={sourceSectionRef}>
            <header className={styles.panelHead}>
              <span><strong>感知源</strong><small>注册发现与用户过滤后的实际来源</small></span>
              <StatusChip status={data?.registry_available ? 'enabled' : 'missing'}>{data?.registry_available ? `${sources.length} 个已注册` : '注册模块缺失'}</StatusChip>
            </header>

            {sources.length ? <div className={styles.sourceList}>
              {sources.map((source) => {
                const open = previewSource === source.id
                return <article className={`${styles.sourceRow} ${source.active_for_main_agent ? styles.sourceActive : ''}`} key={source.id}>
                  <div className={styles.sourceMain}>
                    <span className={styles.sourceIcon}><Activity size={19} /></span>
                    <span className={styles.sourceCopy}>
                      <span className={styles.sourceTitle}><h3>{source.display_name || source.name}</h3><StatusChip status={source.status === 'active' ? 'enabled' : source.status === 'filtered' ? 'paused' : 'warning'}>{statusLabels[source.status] || source.status}</StatusChip></span>
                      <p>{source.description || '该来源尚未注册任何可注入数据。'}</p>
                    </span>
                    <span className={styles.sourceActions}>
                      <button type="button" onClick={() => setPreviewSource(open ? null : source.id)}><Eye size={14} />数据预览</button>
                      <button type="button" onClick={goToSettings}><Settings2 size={14} />注入设置</button>
                      <button type="button" disabled title="当前后端尚未提供手动采集 API"><RefreshCw size={14} />测试采集</button>
                      <button className={styles.moreButton} type="button" disabled aria-label={`${source.name} 更多操作`}><MoreVertical size={16} /></button>
                    </span>
                  </div>
                  <div className={styles.sourceMeta}>
                    <span>模块 ID：{source.name}</span>
                    <span>健康状态：{source.health || '异常'}</span>
                    <span>注册数据：{source.registered_items ?? source.files} 项</span>
                    <span>注入数据：{source.injected_items || 0} 项</span>
                    <span>最后更新：{source.recent_update || (source.updated_at ? formatDateTime(source.updated_at) : '未知')}</span>
                    <span>注入范围：主智能体 · {user}</span>
                  </div>
                  {open && <div className={styles.sourcePreview}>
                    <strong>{source.valid ? '标准数据文件' : '模块校验结果'}</strong>
                    {source.valid && source.data_md ? <ul><li><FileText size={13} /><code>{source.data_md}</code></li></ul> : <p>{source.error || 'sense.json 配置无效。'}</p>}
                    {source.start_update && <p>更新入口：<code>{source.start_update}</code></p>}
                  </div>}
                </article>
              })}
            </div> : <EmptyPanel title="尚无感知源" description="在 global_sense 下创建模块目录并写入采集数据后，刷新即可完成热发现。" icon={<Activity size={21} />} />}
          </section>

          <section className={styles.panel}>
            <header className={styles.panelHead}><span><strong>最近更新</strong><small>按感知数据文件的最后修改时间排序</small></span></header>
            {recentSources.length ? <div className={styles.updateTableWrap}><table className={styles.updateTable}>
              <thead><tr><th>来源名称</th><th>注册数据</th><th>更新时间</th><th>状态</th></tr></thead>
              <tbody>{recentSources.map((source) => <tr key={source.id}>
                <td>{source.display_name || source.name}</td>
                <td>{source.valid ? source.data_md : source.error || '配置无效'}</td>
                <td>{source.recent_update || (source.updated_at ? formatDateTime(source.updated_at) : '未知')}</td>
                <td><StatusChip status={source.status === 'active' ? 'saved' : source.status === 'filtered' ? 'paused' : 'warning'}>{statusLabels[source.status] || source.status}</StatusChip></td>
              </tr>)}</tbody>
            </table></div> : <EmptyPanel title="暂无更新记录" description="感知模块写入数据后，这里会显示真实文件更新时间。" />}
          </section>
        </div>

        <aside className={`${styles.panel} ${styles.injectionPanel}`} ref={injectionSectionRef}>
          <header className={styles.panelHead}><span><strong>当前注入</strong><small>主智能体系统提示词中的实时镜像</small></span><StatusChip status={injection?.enabled ? 'enabled' : 'paused'}>{injection?.enabled ? '正在注入' : '注入为空'}</StatusChip></header>
          <dl className={styles.injectionFacts}>
            <div><dt><UserRound size={15} />用户</dt><dd>{user}</dd></div>
            <div><dt><Bot size={15} />目标智能体</dt><dd>主智能体</dd></div>
            <div><dt><Activity size={15} />已启用来源</dt><dd>{data?.summary.enabled || 0} / {data?.summary.registered || 0}</dd></div>
            <div><dt><Database size={15} />注册数据项</dt><dd>{registeredData} 项</dd></div>
            <div><dt><Sparkles size={15} />实际注入</dt><dd>{injectedData} 项 · {estimatedTokens} tokens</dd></div>
            <div><dt><FileText size={15} />注入位置</dt><dd>{injection?.prompt_position || 'System Prompt / Global Sense'}</dd></div>
          </dl>
          <div className={styles.previewBlock}>
            <div><strong>注入内容预览</strong><span>{injection?.injected_chars || 0} 字符{injection?.truncated ? ' · 已按预算截断' : ''}</span></div>
            {injection?.preview ? <pre>{injection.preview}{injection.preview_truncated ? '\n…' : ''}</pre> : <div className={styles.previewEmpty}><Eye size={19} /><span>当前没有感知数据进入系统提示词</span></div>}
          </div>
          <div className={styles.injectionActions}>
            <button type="button" className="module-btn primary" onClick={() => injectionSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><Eye size={15} />预览最终 Prompt</button>
            <button type="button" className="module-btn" onClick={goToSettings}><Settings2 size={15} />管理注入范围</button>
          </div>
        </aside>
      </div>
    </ModuleFrame>
  )
}
