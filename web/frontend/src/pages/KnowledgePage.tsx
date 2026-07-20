import { type ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Database, Eye, FileText, Layers3, LoaderCircle, Pencil, RefreshCw, Save, Search, Share2, Trash2, Upload, UserRound, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { deleteKnowledgeDocument, getKnowledge, getKnowledgeDocument, putKnowledgeDocument } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatBytes, formatDateTime, MetricCard, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import type { KnowledgeDocumentSummary, SessionsResponse } from '../types/api'

type Scope = 'all' | 'user' | 'shared' | 'global'
type EditableScope = 'user' | 'shared'
type EditorMode = 'markdown' | 'preview'

const scopeLabels: Record<Exclude<Scope, 'all'>, string> = { user: '用户层', shared: '共享层', global: '全局层' }

function basename(path: string) {
  return path.split('/').at(-1) || path
}

function documentKey(document: KnowledgeDocumentSummary) {
  return `${document.scope}:${document.relative_path}`
}

export function KnowledgePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [scope, setScope] = useState<Scope>('all')
  const [queryText, setQueryText] = useState('')
  const [selected, setSelected] = useState<{ scope: string; path: string } | null>(null)
  const [draft, setDraft] = useState('')
  const [editorMode, setEditorMode] = useState<EditorMode>('preview')
  const [hasIndexChanges, setHasIndexChanges] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importScope, setImportScope] = useState<EditableScope>('user')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const query = useQuery({ queryKey: ['knowledge', user], queryFn: () => getKnowledge(user), enabled: Boolean(user) })
  const documentQuery = useQuery({
    queryKey: ['knowledge-document', user, selected?.scope, selected?.path],
    queryFn: () => getKnowledgeDocument(user, selected!.scope, selected!.path),
    enabled: Boolean(user && selected),
  })

  useEffect(() => {
    if (documentQuery.data) {
      setDraft(documentQuery.data.content)
    }
  }, [documentQuery.data])
  useEffect(() => {
    setEditorMode('preview')
  }, [selected?.scope, selected?.path])

  const data = query.data
  const documents = useMemo(() => {
    const term = queryText.trim().toLocaleLowerCase()
    return (data?.documents || []).filter((document) => (
      (scope === 'all' || document.scope === scope)
      && (!term || `${document.title} ${document.relative_path}`.toLocaleLowerCase().includes(term))
    ))
  }, [data?.documents, queryText, scope])
  const selectedSummary = data?.documents.find((document) => document.scope === selected?.scope && document.relative_path === selected?.path)
  const readOnly = selected?.scope === 'global'

  const saveMutation = useMutation({
    mutationFn: () => putKnowledgeDocument(user, selected!.scope, selected!.path, draft),
    onSuccess: async () => {
      setHasIndexChanges(true)
      setEditorMode('preview')
      await client.invalidateQueries({ queryKey: ['knowledge', user] })
      await documentQuery.refetch()
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () => deleteKnowledgeDocument(user, selected!.scope, selected!.path),
    onSuccess: async () => {
      setSelected(null)
      setDraft('')
      setHasIndexChanges(true)
      await client.invalidateQueries({ queryKey: ['knowledge', user] })
    },
  })
  const importMutation = useMutation({
    mutationFn: async ({ file, targetScope }: { file: File; targetScope: EditableScope }) => putKnowledgeDocument(user, targetScope, file.name, await file.text()),
    onSuccess: async (result, variables) => {
      setHasIndexChanges(true)
      setScope(variables.targetScope)
      setSelected({ scope: variables.targetScope, path: String(result.relative_path || variables.file.name) })
      await client.invalidateQueries({ queryKey: ['knowledge', user] })
    },
  })

  const refreshFromConversation = () => {
    const latestSessionId = client.getQueryData<SessionsResponse>(['sessions', user])?.sessions[0]?.session_id || ''
    const prompt = '请刷新用户层、共享层和全局层的全部知识库索引，并在完成后向我报告索引结果。'
    setHasIndexChanges(false)
    navigate(`/chat?user=${encodeURIComponent(user)}${latestSessionId ? `&session=${encodeURIComponent(latestSessionId)}` : ''}&prompt=${encodeURIComponent(prompt)}`)
  }

  const chooseImportScope = (targetScope: EditableScope) => {
    setImportScope(targetScope)
    setImportOpen(false)
    window.setTimeout(() => fileInputRef.current?.click(), 0)
  }
  const handleImportChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.currentTarget.value = ''
    if (file) importMutation.mutate({ file, targetScope: importScope })
  }

  const selectDocument = (document: KnowledgeDocumentSummary) => {
    setSelected({ scope: document.scope, path: document.relative_path })
    setEditorMode('preview')
  }

  return (
    <ModuleFrame
      kicker="File Knowledge & Retrieval"
      title="知识库"
      description="列出用户层、共享层、全局层的完整文件库存，并按当前选中的层级执行搜索。"
      actions={<div className="knowledge-header-actions">
        {hasIndexChanges && <span className="knowledge-index-warning">当前知识文件已更新，请提醒智能体刷新索引</span>}
        {hasIndexChanges && <button type="button" className="module-btn" onClick={() => setHasIndexChanges(false)}>我知道了，不用刷新</button>}
        <button type="button" className="module-btn" disabled={query.isFetching} onClick={refreshFromConversation}>
          {query.isFetching ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}提醒智能体刷新全部知识库索引
        </button>
        <div className="knowledge-import-wrap">
          <button type="button" className="module-btn primary" onClick={() => setImportOpen((value) => !value)} disabled={importMutation.isPending}>
            {importMutation.isPending ? <LoaderCircle size={15} className="spin" /> : <Upload size={15} />}导入知识文件
          </button>
          {importOpen && <div className="knowledge-import-popover" role="menu">
            <strong>选择导入层级</strong>
            <button type="button" onClick={() => chooseImportScope('user')}><UserRound size={15} />导入用户层</button>
            <button type="button" onClick={() => chooseImportScope('shared')}><Share2 size={15} />导入共享层</button>
          </div>}
        </div>
        <input ref={fileInputRef} hidden type="file" accept=".md,.txt,.json" onChange={handleImportChange} />
      </div>}
    >
      {query.isError && <ModuleError message="知识库读取失败，请检查服务状态或重新登录。" />}
      {importMutation.isError && <ModuleError message={importMutation.error instanceof Error ? importMutation.error.message : '知识文件导入失败'} />}
      <div className="knowledge-searchbar">
        <span className="search-field"><Search size={16} /><input value={queryText} onChange={(event) => setQueryText(event.target.value)} placeholder={`${scope === 'all' ? '全部层级' : scopeLabels[scope]}：搜索文件名或标题…`} /></span>
        <button type="button" className="module-btn" onClick={() => setQueryText('')}>清除</button>
      </div>

      <section className="metric-strip knowledge-metrics">
        <MetricCard label="文件总数" value={data?.summary.documents ?? '—'} detail="有效文档" symbol={<Database size={16} />} />
        <MetricCard label="用户层" value={data?.summary.user_documents ?? '—'} detail={`users/${user}/knowledge`} symbol={<FileText size={16} />} />
        <MetricCard label="共享层" value={data?.summary.shared_documents ?? '—'} detail="shared_knowledge" symbol={<Share2 size={16} />} />
        <MetricCard label="全局层" value={data?.summary.global_documents ?? '—'} detail="global_knowledge" symbol={<Layers3 size={16} />} />
      </section>

      <div className="module-toolbar knowledge-layer-toolbar">
        <div className="module-tabs">{(['all', 'user', 'shared', 'global'] as const).map((value) => <button type="button" key={value} className={`module-tab-btn ${scope === value ? 'active' : ''}`} onClick={() => setScope(value)}>{value === 'all' ? '全部' : scopeLabels[value]}</button>)}</div>
        <div className="toolbar-spacer" />
        <span className="toolbar-note">当前显示 {documents.length} / {data?.summary.documents || 0}</span>
      </div>

      <div className="module-grid knowledge-grid knowledge-grid-editor">
        <article className="panel table-panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">K</span><span><strong>知识文件</strong><span>只展示元数据，点击文件进入编辑查看</span></span></div><span className="panel-count">{documents.length}</span></div>
          {documents.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>名称</th><th>层级</th><th>主智能体</th><th>路径</th><th>大小</th><th>更新时间</th></tr></thead><tbody>{documents.map((document) => <tr key={documentKey(document)} className={selected && selected.scope === document.scope && selected.path === document.relative_path ? 'knowledge-row-selected' : ''} onClick={() => selectDocument(document)}><td><span className="table-main"><span className="table-icon"><FileText size={14} /></span><span><strong>{document.title}</strong><span>{basename(document.relative_path)}</span></span></span></td><td><span className={`scope-tag ${document.scope}`}>{scopeLabels[document.scope as Exclude<Scope, 'all'>] || document.scope}</span></td><td><StatusChip status={document.active_for_main_agent ? 'enabled' : 'paused'}>{document.active_for_main_agent ? '已启用' : '已过滤'}</StatusChip></td><td className="path-cell">{document.relative_path}</td><td>{formatBytes(document.size)}</td><td>{formatDateTime(document.updated_at)}</td></tr>)}</tbody></table></div> : <EmptyPanel title="没有匹配的知识文件" description={queryText ? '调整搜索词或切换层级后重试。' : '当前知识目录中还没有可索引文件。'} icon={<BookOpen size={21} />} />}
        </article>

        <aside className="knowledge-editor panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><FileText size={15} /></span><span><strong>编辑查看</strong><span>{readOnly ? '全局层只支持查看' : 'Markdown 编辑与预览'}</span></span></div>{selected && <button type="button" className="knowledge-close-button" aria-label="关闭编辑查看" onClick={() => setSelected(null)}><X size={16} /></button>}</div>
          {!selected || !selectedSummary ? <div className="knowledge-editor-empty"><BookOpen size={25} /><strong>选择知识文件</strong><span>点击左侧知识文件后，可在此处编辑或预览。</span></div> : <>
            <div className="knowledge-editor-file"><span className="knowledge-file-avatar">K</span><span><strong>{basename(selected.path)}</strong><small>{selectedSummary.relative_path}</small></span><span className={`scope-tag ${selected.scope}`}>{scopeLabels[selected.scope as Exclude<Scope, 'all'>] || selected.scope}</span><StatusChip status={selectedSummary.active_for_main_agent ? 'enabled' : 'paused'}>{selectedSummary.active_for_main_agent ? '已启用' : '已过滤'}</StatusChip></div>
            <div className="knowledge-editor-toolbar"><div className="knowledge-mode-switch"><button type="button" className={`module-btn ${editorMode === 'preview' ? 'active' : ''}`} onClick={() => setEditorMode('preview')}><Eye size={14} />预览</button>{!readOnly && <button type="button" className={`module-btn ${editorMode === 'markdown' ? 'active' : ''}`} onClick={() => setEditorMode('markdown')}><Pencil size={14} />编辑</button>}</div>{!readOnly && <div className="module-actions"><button type="button" className="module-btn primary" disabled={saveMutation.isPending || documentQuery.isFetching || editorMode !== 'markdown'} onClick={() => saveMutation.mutate()}><Save size={14} />保存编辑</button><button type="button" className="module-btn danger" disabled={deleteMutation.isPending} onClick={() => { if (window.confirm('删除此知识文件？')) deleteMutation.mutate() }}><Trash2 size={14} />删除此知识</button></div>}</div>
            {saveMutation.isError && <ModuleError message={saveMutation.error instanceof Error ? saveMutation.error.message : '知识文件保存失败'} />}
            {deleteMutation.isError && <ModuleError message={deleteMutation.error instanceof Error ? deleteMutation.error.message : '知识文件删除失败'} />}
            <div className="knowledge-editor-content">{documentQuery.isLoading ? <div className="center-state">正在加载知识正文…</div> : editorMode === 'markdown' ? <textarea className="knowledge-markdown-editor" value={draft} readOnly={readOnly} spellCheck={false} onChange={(event) => setDraft(event.target.value)} /> : <article className="knowledge-markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown></article>}</div>
            <div className="knowledge-editor-footer"><span>共 {draft.split('\n').length} 行</span><span>约 {draft.trim().length} 字符</span><span>最后更新：{formatDateTime(documentQuery.data?.updated_at || selectedSummary.updated_at)}</span></div>
          </>}
        </aside>
      </div>
    </ModuleFrame>
  )
}
