import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Boxes,
  Download,
  Eye,
  FileCode2,
  LoaderCircle,
  Pencil,
  Save,
  Search,
  Share2,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
  Wrench,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOutletContext } from 'react-router-dom'
import {
  deleteSkill,
  getSkillDocument,
  getSkillDownloadUrl,
  getSkills,
  putSkillDocument,
  setSkillEnabled,
  uploadUserSkills,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { RefreshActionButton } from '../components/ModuleUi'
import type { SkillCatalogItem, SkillCategory } from '../types/api'
import styles from './SkillsPage.module.css'

type SkillFilter = 'all' | SkillCategory
type ViewMode = 'preview' | 'edit'

const categoryMeta: Record<SkillCategory, { label: string; icon: ReactNode }> = {
  builtin: { label: '基础插件', icon: <Wrench size={16} /> },
  shared: { label: '共享技能', icon: <Share2 size={16} /> },
  agent_generated: { label: '智能体生成技能', icon: <Sparkles size={16} /> },
  user_created: { label: '用户自建技能', icon: <UserRound size={16} /> },
}

const filters: Array<{ value: SkillFilter; label: string }> = [
  { value: 'all', label: '全部技能' },
  { value: 'builtin', label: '基础插件' },
  { value: 'shared', label: '共享技能' },
  { value: 'agent_generated', label: '智能体生成技能' },
  { value: 'user_created', label: '用户自建技能' },
]

function versionLabel(version: string) {
  const normalized = String(version || '').trim().replace(/^v/i, '')
  return normalized ? `v${normalized}` : '未声明版本'
}

function skillCode(skill: SkillCatalogItem) {
  return (skill.title || skill.name)
    .split(/[_\-\s/]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.slice(0, 1).toLocaleUpperCase())
    .join('') || 'SK'
}

export function SkillsPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [activeFilter, setActiveFilter] = useState<SkillFilter>('all')
  const [searchText, setSearchText] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [mode, setMode] = useState<ViewMode>('preview')
  const [draft, setDraft] = useState('')
  const [deleteTarget, setDeleteTarget] = useState('')
  const [feedback, setFeedback] = useState('')
  const [uploadValidationError, setUploadValidationError] = useState('')
  const uploadInputRef = useRef<HTMLInputElement>(null)

  const skillsQuery = useQuery({
    queryKey: ['skills', user],
    queryFn: () => getSkills(user),
    enabled: Boolean(user),
  })
  const items = skillsQuery.data?.items ?? []
  const filteredItems = useMemo(() => {
    const keyword = searchText.trim().toLocaleLowerCase()
    return items.filter((item) => (
      (activeFilter === 'all' || item.category === activeFilter)
      && (!keyword || [item.title, item.name, item.description, item.path]
        .some((value) => String(value || '').toLocaleLowerCase().includes(keyword)))
    ))
  }, [activeFilter, items, searchText])
  const selectedSkill = filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? null
  const documentQuery = useQuery({
    queryKey: ['skill-document', user, selectedSkill?.category, selectedSkill?.name],
    queryFn: () => getSkillDocument(user, selectedSkill!.category, selectedSkill!.name),
    enabled: Boolean(user && selectedSkill),
  })

  useEffect(() => {
    setDraft(documentQuery.data?.content ?? '')
  }, [documentQuery.data])
  useEffect(() => {
    setMode('preview')
    setDeleteTarget('')
  }, [selectedSkill?.id])

  const toggleMutation = useMutation({
    mutationFn: ({ skill, enabled }: { skill: SkillCatalogItem; enabled: boolean }) => setSkillEnabled(user, skill.category, skill.name, enabled),
    onSuccess: async (result, variables) => {
      setFeedback(`${variables.skill.title || variables.skill.name} 已${result.enabled ? '启用' : '禁用'}`)
      await queryClient.invalidateQueries({ queryKey: ['skills', user] })
    },
  })
  const saveMutation = useMutation({
    mutationFn: () => putSkillDocument(user, selectedSkill!.category, selectedSkill!.name, draft),
    onSuccess: async (result) => {
      setDraft(result.content)
      setMode('preview')
      setFeedback(`已保存 ${selectedSkill?.title || selectedSkill?.name}`)
      await queryClient.invalidateQueries({ queryKey: ['skills', user] })
      await documentQuery.refetch()
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () => deleteSkill(user, selectedSkill!.category, selectedSkill!.name),
    onSuccess: async (result) => {
      setFeedback(`已删除技能 ${result.name}`)
      setSelectedId('')
      setDeleteTarget('')
      await queryClient.invalidateQueries({ queryKey: ['skills', user] })
    },
  })
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadUserSkills(user, file),
    onSuccess: async (result) => {
      const names = result.installed.map((item) => item.title || item.name).join('、')
      setActiveFilter('user_created')
      setSearchText('')
      setSelectedId(result.installed[0] ? `user_created:${result.installed[0].name}` : '')
      setFeedback(`已安装 ${result.count} 个用户技能${names ? `：${names}` : ''}`)
      await queryClient.invalidateQueries({ queryKey: ['skills', user] })
    },
    onSettled: () => {
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    },
  })

  const chooseSkillArchive = () => {
    setFeedback('')
    setUploadValidationError('')
    uploadMutation.reset()
    if (uploadInputRef.current) uploadInputRef.current.value = ''
    uploadInputRef.current?.click()
  }
  const uploadSkillArchive = (file: File | undefined) => {
    if (!file) return
    if (!file.name.toLocaleLowerCase().endsWith('.zip')) {
      setUploadValidationError('用户技能只支持 ZIP 压缩包')
      if (uploadInputRef.current) uploadInputRef.current.value = ''
      return
    }
    setUploadValidationError('')
    uploadMutation.mutate(file)
  }

  const changeFilter = (filter: SkillFilter) => {
    setActiveFilter(filter)
    setSelectedId('')
    setFeedback('')
  }
  const refresh = async () => {
    setFeedback('')
    await skillsQuery.refetch()
    if (selectedSkill) await documentQuery.refetch()
  }
  const summary = skillsQuery.data?.catalog_summary

  return (
    <div className="view module-view active">
      <div className="module-shell">
        <main className={`module-inner ${styles.page}`}>
          <header className={styles.header}>
            <div>
              <div className={styles.titleRow}><h2>工具与技能</h2><span>{user || '未选择用户'}</span></div>
              <p>统一查看基础插件、共享技能与两类用户技能；可用范围由当前用户的插件和共享技能白名单控制。</p>
            </div>
            <div className={styles.headerActions}>
              <RefreshActionButton pending={skillsQuery.isFetching} label="刷新技能库" pendingLabel="刷新中…" iconSize={16} className={styles.refreshButton} onClick={() => { void refresh() }} />
              <input
                ref={uploadInputRef}
                className={styles.uploadInput}
                type="file"
                accept=".zip,application/zip,application/x-zip-compressed"
                aria-label="选择用户技能 ZIP 压缩包"
                onChange={(event) => uploadSkillArchive(event.target.files?.[0])}
              />
              <button type="button" className={styles.uploadButton} disabled={uploadMutation.isPending || !user} onClick={chooseSkillArchive}>
                {uploadMutation.isPending ? <LoaderCircle size={16} className={styles.spinning} /> : <Upload size={16} />}
                {uploadMutation.isPending ? '正在上传…' : '上传用户技能'}
              </button>
            </div>
          </header>

          <section className={styles.summaryGrid} aria-label="技能统计">
            <SummaryCard label="已注册" value={summary?.total ?? '—'} description="工具 / 技能总数" icon={<Boxes size={19} />} />
            <SummaryCard label="已启用" value={summary?.enabled ?? '—'} description="当前用户可使用" icon={<Sparkles size={19} />} />
            <SummaryCard label="共享技能" value={summary?.shared ?? '—'} description="来自 shared_skills" icon={<Share2 size={19} />} />
            <SummaryCard label="基础插件" value={summary?.builtin ?? '—'} description="来自 plugins" icon={<Wrench size={19} />} />
          </section>

          <section className={styles.registry}>
            <div className={styles.toolbar}>
              <div className={styles.tabs} role="tablist" aria-label="技能分类">
                {filters.map((filter) => <button type="button" role="tab" aria-selected={activeFilter === filter.value} className={activeFilter === filter.value ? styles.active : ''} key={filter.value} onClick={() => changeFilter(filter.value)}>{filter.label}</button>)}
              </div>
              <label className={styles.search}><Search size={16} /><input type="search" aria-label="搜索技能" value={searchText} placeholder="搜索技能名称或描述…" onChange={(event) => setSearchText(event.target.value)} /></label>
            </div>

            {skillsQuery.isError ? <div className={styles.errorBanner}>技能库读取失败，请检查配置文件或服务状态。</div> : null}
            {uploadValidationError || uploadMutation.isError ? <div className={styles.errorBanner} role="alert">{uploadValidationError || (uploadMutation.error instanceof Error ? uploadMutation.error.message : '用户技能上传失败')}</div> : null}
            {feedback ? <div className={styles.feedback} role="status">{feedback}</div> : null}
            <div className={styles.workspace}>
              <section className={styles.listPanel} aria-label="技能列表">
                <div className={styles.listHeading}><div><h3>技能列表</h3><p>当前显示 {filteredItems.length} 个项目</p></div><span>{filteredItems.length}</span></div>
                <div className={styles.skillList}>
                  {skillsQuery.isLoading ? <EmptyState icon={<LoaderCircle size={26} className={styles.spinning} />} title="正在读取技能库" description="正在解析插件与 Prompt 技能注册信息。" /> : null}
                  {!skillsQuery.isLoading && filteredItems.map((skill) => <SkillListItem
                    key={skill.id}
                    skill={skill}
                    selected={selectedSkill?.id === skill.id}
                    pending={toggleMutation.isPending && toggleMutation.variables?.skill.id === skill.id}
                    onSelect={() => { setSelectedId(skill.id); setFeedback('') }}
                    onToggle={(enabled) => toggleMutation.mutate({ skill, enabled })}
                  />)}
                  {!skillsQuery.isLoading && filteredItems.length === 0 ? <EmptyState icon={<FileCode2 size={26} />} title={searchText.trim() ? '没有找到匹配的技能' : '当前分类暂无技能'} description={searchText.trim() ? '请尝试其他名称、描述或路径关键词。' : '该技能目录中尚未注册有效的 SKILL.md。'} /> : null}
                </div>
              </section>

              <aside className={styles.detailPanel} aria-label="技能查看">
                {selectedSkill ? <SkillDetail
                  skill={selectedSkill}
                  content={draft}
                  loading={documentQuery.isLoading}
                  mode={mode}
                  confirmingDelete={deleteTarget === selectedSkill.id}
                  busy={saveMutation.isPending || deleteMutation.isPending || toggleMutation.isPending}
                  error={documentQuery.isError || saveMutation.isError || deleteMutation.isError || toggleMutation.isError
                    ? String((documentQuery.error || saveMutation.error || deleteMutation.error || toggleMutation.error) instanceof Error
                      ? (documentQuery.error || saveMutation.error || deleteMutation.error || toggleMutation.error as Error).message
                      : '技能操作失败')
                    : ''}
                  downloadUrl={getSkillDownloadUrl(user, selectedSkill.category, selectedSkill.name)}
                  onModeChange={setMode}
                  onDraftChange={setDraft}
                  onSave={() => saveMutation.mutate()}
                  onToggle={(enabled) => toggleMutation.mutate({ skill: selectedSkill, enabled })}
                  onRequestDelete={() => setDeleteTarget(selectedSkill.id)}
                  onCancelDelete={() => setDeleteTarget('')}
                  onConfirmDelete={() => deleteMutation.mutate()}
                /> : <EmptyState icon={<Wrench size={28} />} title="暂无可查看的技能" description="当前分类中没有符合条件的技能。" detail />}
              </aside>
            </div>
          </section>
        </main>
      </div>
    </div>
  )
}

function SummaryCard({ label, value, description, icon }: { label: string; value: ReactNode; description: string; icon: ReactNode }) {
  return <article className={styles.summaryCard}><i>{icon}</i><span><small>{label}</small><strong>{value}</strong><p>{description}</p></span></article>
}

function SkillListItem({ skill, selected, pending, onSelect, onToggle }: { skill: SkillCatalogItem; selected: boolean; pending: boolean; onSelect: () => void; onToggle: (enabled: boolean) => void }) {
  return <article className={`${styles.skillItem} ${selected ? styles.selected : ''}`}>
    <button type="button" className={styles.skillSelect} onClick={onSelect}>
      <span className={styles.skillMark}>{skillCode(skill)}</span>
      <span className={styles.skillContent}><strong>{skill.title || skill.name}</strong><small>{skill.description || '未提供技能描述'}</small><span><em className={`${styles.categoryTag} ${styles[skill.category]}`}>{categoryMeta[skill.category].label}</em><b>{versionLabel(skill.version)}</b></span></span>
      {!skill.toggleable ? <span className={styles.chevron}>›</span> : null}
    </button>
    {skill.toggleable ? <label className={styles.itemSwitch} title={skill.enabled ? '禁用' : '启用'}><input type="checkbox" checked={skill.enabled} disabled={pending} aria-label={`${skill.enabled ? '禁用' : '启用'} ${skill.title || skill.name}`} onChange={(event) => onToggle(event.target.checked)} /><span /></label> : null}
  </article>
}

function SkillDetail({ skill, content, loading, mode, confirmingDelete, busy, error, downloadUrl, onModeChange, onDraftChange, onSave, onToggle, onRequestDelete, onCancelDelete, onConfirmDelete }: {
  skill: SkillCatalogItem
  content: string
  loading: boolean
  mode: ViewMode
  confirmingDelete: boolean
  busy: boolean
  error: string
  downloadUrl: string
  onModeChange: (mode: ViewMode) => void
  onDraftChange: (content: string) => void
  onSave: () => void
  onToggle: (enabled: boolean) => void
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  return <article className={styles.detail}>
    <header className={styles.detailHeader}>
      <div className={styles.detailIdentity}><span className={styles.detailMark}>{skillCode(skill)}</span><div><h3>{skill.title || skill.name}</h3><p>{skill.description || '未提供技能描述'}</p></div></div>
      <span className={`${styles.status} ${skill.enabled ? styles.enabled : styles.disabled}`}><i />{skill.enabled ? '已启用' : '已禁用'}</span>
    </header>

    <div className={styles.detailActions}>
      <button type="button" className={mode === 'preview' ? styles.actionActive : ''} onClick={() => onModeChange('preview')}><Eye size={15} />预览</button>
      {skill.editable ? <>
        <button type="button" className={mode === 'edit' ? styles.actionActive : ''} onClick={() => onModeChange('edit')}><Pencil size={15} />编辑</button>
        <button type="button" className={styles.primaryAction} disabled={busy || mode !== 'edit'} onClick={onSave}><Save size={15} />保存</button>
        <button type="button" className={styles.dangerAction} disabled={busy} onClick={onRequestDelete}><Trash2 size={15} />删除</button>
      </> : <>
        <a href={downloadUrl} download><Download size={15} />下载</a>
        {skill.toggleable ? <button type="button" className={skill.enabled ? styles.dangerAction : styles.primaryAction} disabled={busy} onClick={() => onToggle(!skill.enabled)}>{skill.enabled ? '禁用' : '启用'}</button> : null}
      </>}
    </div>

    {confirmingDelete ? <div className={styles.deleteConfirm} role="alertdialog" aria-label="确认删除用户技能"><span><strong>确认删除 {skill.title || skill.name}？</strong><small>将永久删除整个用户技能包，无法撤销。</small></span><div><button type="button" onClick={onCancelDelete} disabled={busy}>取消</button><button type="button" className={styles.confirmDelete} onClick={onConfirmDelete} disabled={busy}>{busy ? '正在删除…' : '确认删除'}</button></div></div> : null}
    {error ? <div className={styles.errorBanner}>{error}</div> : null}

    <div className={styles.metaRows}>
      <div><strong>分类</strong><span className={`${styles.categoryTag} ${styles[skill.category]}`}>{categoryMeta[skill.category].icon}{categoryMeta[skill.category].label}</span></div>
      <div><strong>版本</strong><span>{versionLabel(skill.version)}</span></div>
      <div><strong>路径</strong><code>{skill.path}</code></div>
      <div><strong>管理权限</strong><span>{skill.editable ? '当前用户可编辑、保存和删除' : skill.toggleable ? '可预览、下载，并通过用户白名单启用或禁用' : '只读技能'}</span></div>
    </div>

    <section className={styles.documentPanel}>
      <div className={styles.documentHeading}><span><FileCode2 size={15} />SKILL.md</span><small>{content.split('\n').length} 行 · {content.length} 字符</small></div>
      <div className={styles.documentContent}>{loading ? <EmptyState icon={<LoaderCircle size={24} className={styles.spinning} />} title="正在加载技能内容" description="" /> : mode === 'edit' && skill.editable ? <textarea aria-label="技能 Markdown 编辑器" value={content} spellCheck={false} onChange={(event) => onDraftChange(event.target.value)} /> : <article className={styles.markdown}><ReactMarkdown remarkPlugins={[remarkGfm]}>{content || '该技能没有可预览的正文。'}</ReactMarkdown></article>}</div>
    </section>
  </article>
}

function EmptyState({ icon, title, description, detail = false }: { icon: ReactNode; title: string; description: string; detail?: boolean }) {
  return <div className={`${styles.emptyState} ${detail ? styles.detailEmpty : ''}`}>{icon}<strong>{title}</strong>{description ? <p>{description}</p> : null}</div>
}
