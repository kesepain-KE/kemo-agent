import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Database, FileText, FolderTree, Layers3, PlugZap, RefreshCw, Search } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getKnowledge } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import {
  EmptyPanel,
  formatBytes,
  formatDateTime,
  MetricCard,
  ModuleError,
  ModuleFrame,
  StatusChip,
} from '../components/ModuleUi'

type Scope = 'all' | 'user' | 'global'

export function KnowledgePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const [scope, setScope] = useState<Scope>('all')
  const [queryText, setQueryText] = useState('')
  const query = useQuery({ queryKey: ['knowledge', user], queryFn: () => getKnowledge(user), enabled: Boolean(user) })
  const documents = useMemo(() => {
    const term = queryText.trim().toLocaleLowerCase()
    return (query.data?.documents || []).filter((document) => (
      (scope === 'all' || document.scope === scope)
      && (!term || `${document.title} ${document.relative_path}`.toLocaleLowerCase().includes(term))
    ))
  }, [query.data?.documents, queryText, scope])
  const data = query.data

  return (
    <ModuleFrame
      kicker="File Knowledge & Retrieval"
      title="知识库"
      description="默认使用当前用户与全局目录的纯文件索引；外接图谱未连接时，基础检索仍保持完整可用。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新索引状态</button>}
    >
      {query.isError && <ModuleError />}
      <div className="knowledge-searchbar">
        <span className="search-field"><Search size={16} /><input value={queryText} onChange={(event) => setQueryText(event.target.value)} placeholder="搜索文件名或标题…" /></span>
        <button className="module-btn" onClick={() => setQueryText('')}>清除</button>
      </div>
      <section className="metric-strip">
        <MetricCard label="文件索引" value={data?.summary.documents ?? '—'} detail="有效文档" symbol={<Database size={16} />} />
        <MetricCard label="用户层" value={data?.summary.user_documents ?? '—'} detail={`users/${user}/knowledge`} symbol={<FileText size={16} />} />
        <MetricCard label="全局层" value={data?.summary.global_documents ?? '—'} detail="global_knowledge" symbol={<Layers3 size={16} />} />
        <MetricCard label="检索模式" value={data?.enabled ? '已启用' : '已停用'} detail="本地文件索引" symbol={<Search size={16} />} tone={data?.enabled ? 'success' : 'muted'} />
      </section>

      <section className="index-summary panel">
        <div className="index-copy">
          <span className="panel-title-icon">I</span>
          <span><strong>文件索引管理</strong><small>读取用户层、全局层中的 Markdown、TXT 与 JSON</small></span>
        </div>
        <div className="index-progress">
          <span><b>{query.isFetching ? '正在读取' : data?.enabled ? '索引可用' : '索引停用'}</b><small>按请求实时构建，不维护第二份缓存</small></span>
          <span className="progress-line"><i style={{ width: query.isFetching ? '45%' : data?.enabled ? '100%' : '0%' }} /></span>
        </div>
        <StatusChip status={data?.enabled ? 'enabled' : 'paused'} />
      </section>

      <div className="module-toolbar">
        <div className="module-tabs">
          {([['all', '全部'], ['user', '用户层'], ['global', '全局层']] as const).map(([value, label]) => (
            <button key={value} className={`module-tab-btn ${scope === value ? 'active' : ''}`} onClick={() => setScope(value)}>{label}</button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <span className="toolbar-note">当前显示 {documents.length} / {data?.summary.documents || 0}</span>
      </div>

      <div className="module-grid knowledge-grid">
        <div>
          <article className="panel table-panel">
            <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">K</span><span><strong>知识文件</strong><span>只展示元数据，不通过列表接口返回正文</span></span></div><span className="panel-count">{documents.length}</span></div>
            {documents.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>名称</th><th>层级</th><th>路径</th><th>大小</th><th>更新时间</th></tr></thead><tbody>
              {documents.map((document) => <tr key={`${document.scope}:${document.relative_path}`}><td><span className="table-main"><span className="table-icon"><FileText size={14} /></span><span><strong>{document.title}</strong><span>{document.relative_path.split('/').at(-1)}</span></span></span></td><td><span className="scope-tag">{document.scope === 'user' ? '用户层' : '全局层'}</span></td><td className="path-cell">{document.relative_path}</td><td>{formatBytes(document.size)}</td><td>{formatDateTime(document.updated_at)}</td></tr>)}
            </tbody></table></div> : <EmptyPanel title="没有匹配的知识文件" description={queryText ? '调整搜索词或切换层级后重试。' : '当前知识目录中还没有可索引文件。'} icon={<FolderTree size={21} />} />}
          </article>
        </div>
        <aside>
          <article className="panel">
            <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">C</span><span><strong>知识集合</strong><span>按运行时检索优先级组织</span></span></div></div>
            <div className="panel-body collection-grid">
              <article className="collection-card"><span className="collection-icon"><FileText size={15} /></span><strong>当前用户知识</strong><span>{data?.summary.user_documents || 0} 个文档 · 优先检索</span></article>
              <article className="collection-card"><span className="collection-icon"><Layers3 size={15} /></span><strong>全局共享知识</strong><span>{data?.summary.global_documents || 0} 个文档 · 兜底检索</span></article>
            </div>
          </article>
          <article className="panel extension-card">
            <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><PlugZap size={15} /></span><span><strong>外接项目 · kemo-graph</strong><span>可选扩展，不属于核心索引</span></span></div><StatusChip status="not_connected" /></div>
            <div className="panel-body"><p className="panel-copy">当前未连接图谱扩展，检索自动保持为纯文件模式，不影响现有知识注入。</p></div>
          </article>
        </aside>
      </div>
    </ModuleFrame>
  )
}
