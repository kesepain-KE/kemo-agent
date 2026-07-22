import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, CalendarDays, ChevronLeft, ChevronRight, Eye, FileText, LoaderCircle, Plus, RotateCcw, Save, Search, Trash2, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOutletContext } from 'react-router-dom'
import { deleteMemory, getImportantMemory, getMemoryItem, getMemorySummary, putMemory, updateImportantMemory } from '../api/client'
import type { ImportantMemoryResponse, MemorySummaryResponse } from '../types/api'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, ModuleError, ModuleFrame, RefreshActionButton } from '../components/ModuleUi'
import styles from './MemoryPage.module.css'

type MemoryTier = 'seven_days' | 'one_month' | 'half_year' | 'important' | 'permanent'

const TABS: MemoryTier[] = ['seven_days', 'one_month', 'half_year', 'important', 'permanent']
const TIER_LABELS: Record<MemoryTier, string> = {
  seven_days: '周记忆',
  one_month: '月记忆',
  half_year: '半年记忆',
  important: '临时重要记忆',
  permanent: '长期记忆',
}
const WEIGHTED_TIERS = new Set<MemoryTier>(['seven_days', 'one_month', 'half_year'])
const PAGE_SIZES = [6]

interface MemoryRow {
  key: string
  filename: string
  tier: MemoryTier
  title: string
  preview: string
  weight: number
  createdAt: string
  updatedAt: string
  lastUsedAt: string | null
  expiresAt: string | null
  lastWeightDate?: string | null
}

function titleFor(filename: string, preview: string) {
  const first = preview.split(/\r?\n/).find((line) => line.trim())?.trim() || filename
  return first.replace(/^#{1,6}\s*/, '').slice(0, 72) || filename
}

function keyFor(tier: MemoryTier, filename: string) {
  return `${tier}:${filename}`
}

function formatUpdated(value: string) {
  if (!value) return '未记录'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}

function toRows(summary: MemorySummaryResponse | undefined, important: ImportantMemoryResponse | undefined): MemoryRow[] {
  const rows = (summary?.items || []).map((item) => {
    const tier = item.tier as MemoryTier
    return {
      key: item.memory_ref || keyFor(tier, item.filename),
      filename: item.filename,
      tier,
      title: titleFor(item.filename, item.preview),
      preview: item.preview,
      weight: item.weight,
      createdAt: item.created_at || item.content_updated_at || item.updated_at,
      updatedAt: item.content_updated_at || item.updated_at,
      lastUsedAt: item.last_used_at,
      expiresAt: item.expires_at,
    }
  })
  if (important) {
    rows.push({
      key: keyFor('important', 'memory_temporary_important.md'),
      filename: 'memory_temporary_important.md',
      tier: 'important',
      title: '临时重要记忆',
      preview: important.content.slice(0, 180),
      weight: 0,
      createdAt: important.updated_at || '',
      updatedAt: important.updated_at || '',
      lastUsedAt: null,
      expiresAt: null,
    })
  }
  return rows
}

function StatCard({ tier, count, active, onClick }: { tier: MemoryTier; count: number; active: boolean; onClick: () => void }) {
  return <button type="button" className={`${styles.statCard} ${active ? styles.statCardActive : ''}`} onClick={onClick}>
    <span className={styles.statTop}><strong>{TIER_LABELS[tier]}</strong><span className={styles.statIcon}>{tier === 'important' ? <FileText size={18} /> : tier === 'permanent' ? <Brain size={18} /> : <CalendarDays size={18} />}</span></span>
    <span className={styles.statCount}>{count}<small>{tier === 'important' ? '文件' : '片段'}</small></span>
  </button>
}

export function MemoryPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const client = useQueryClient()
  const [activeTier, setActiveTier] = useState<MemoryTier>('seven_days')
  const [selectedKey, setSelectedKey] = useState('')
  const [draft, setDraft] = useState('')
  const [original, setOriginal] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [queryText, setQueryText] = useState('')
  const [sort, setSort] = useState<'newest' | 'oldest' | 'weight'>('newest')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(6)
  const [createOpen, setCreateOpen] = useState(false)
  const [newContent, setNewContent] = useState('')
  const [pendingDelete, setPendingDelete] = useState<MemoryRow | null>(null)

  const summary = useQuery({ queryKey: ['memory-summary', user], queryFn: () => getMemorySummary(user), enabled: Boolean(user) })
  const important = useQuery({ queryKey: ['memory-important', user], queryFn: () => getImportantMemory(user), enabled: Boolean(user), retry: false })
  const rows = useMemo(() => toRows(summary.data, important.data), [summary.data, important.data])
  const importantRow = rows.find((row) => row.tier === 'important')
  const selected = activeTier === 'important' ? importantRow : rows.find((row) => row.key === selectedKey)
  const selectedRegular = Boolean(selected && selected.tier !== 'important')
  const item = useQuery({ queryKey: ['memory-item', user, selected?.tier, selected?.filename], queryFn: () => getMemoryItem(user, selected!.tier, selected!.filename), enabled: Boolean(user && selectedRegular && selected) })
  const detailContent = selected?.tier === 'important' ? important.data?.content : item.data?.content
  const activeSelectionKey = selected?.key || ''

  useEffect(() => {
    setDraft('')
    setOriginal('')
    setPreviewing(false)
  }, [activeSelectionKey])
  useEffect(() => {
    if (detailContent !== undefined && activeSelectionKey) {
      setDraft(detailContent)
      setOriginal(detailContent)
    }
  }, [activeSelectionKey, detailContent])

  const visibleRows = useMemo(() => {
    const normalized = queryText.trim().toLocaleLowerCase()
    return rows.filter((row) => row.tier === activeTier && (!normalized || `${row.title} ${row.filename} ${row.preview}`.toLocaleLowerCase().includes(normalized))).sort((a, b) => {
      if (sort === 'weight') return b.weight - a.weight
      const aTime = Date.parse(a.updatedAt) || 0
      const bTime = Date.parse(b.updatedAt) || 0
      return sort === 'oldest' ? aTime - bTime : bTime - aTime
    })
  }, [activeTier, queryText, rows, sort])
  const totalPages = Math.max(1, Math.ceil(visibleRows.length / pageSize))
  const pagedRows = visibleRows.slice((page - 1) * pageSize, page * pageSize)
  useEffect(() => setPage(1), [activeTier, queryText, pageSize])
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [page, totalPages])

  const reload = () => { void summary.refetch(); void important.refetch(); if (selected) void item.refetch() }
  const invalidateMemory = async () => {
    await client.invalidateQueries({ queryKey: ['memory-summary', user] })
    await client.invalidateQueries({ queryKey: ['memory-important', user] })
    if (selected) await client.invalidateQueries({ queryKey: ['memory-item', user, selected.tier, selected.filename] })
  }
  const save = useMutation<unknown, Error>({
    mutationFn: () => selected?.tier === 'important' ? updateImportantMemory(user, draft) : putMemory(user, selected!.filename, draft, selected!.tier),
    onSuccess: async () => { setOriginal(draft); await invalidateMemory() },
  })
  const remove = useMutation({
    mutationFn: () => deleteMemory(user, pendingDelete!.tier, pendingDelete!.filename),
    onSuccess: async () => { setPendingDelete(null); setSelectedKey(''); await invalidateMemory() },
  })
  const create = useMutation({
    mutationFn: () => {
      const filename = `memory_${Date.now()}.md`
      return putMemory(user, filename, newContent.trim(), 'permanent')
    },
    onSuccess: async (result) => {
      const filename = String(result.filename || '')
      setCreateOpen(false)
      setNewContent('')
      setActiveTier('permanent')
      setSelectedKey(filename ? keyFor('permanent', filename) : '')
      await invalidateMemory()
    },
  })

  const counts = TABS.reduce((result, tier) => {
    result[tier] = tier === 'important' ? (important.data ? 1 : 0) : summary.data?.summary[tier as keyof typeof summary.data.summary] || 0
    return result
  }, {} as Record<MemoryTier, number>)
  const changed = draft !== original
  const loadingDetail = Boolean(selected && (selected.tier === 'important' ? important.isLoading : item.isLoading))
  const weightToday = Boolean(item.data?.last_weight_date && item.data.last_weight_date === new Date().toISOString().slice(0, 10))
  const memoryRefreshing = summary.isFetching || important.isFetching

  return <ModuleFrame kicker="Memory & Lifecycle" title="记忆" description="管理智能体不同时间尺度的记忆片段，让 kemo-agent 在对话中持续学习并提供更贴合的帮助。" actions={<div className={styles.headerActions}>
    <RefreshActionButton pending={memoryRefreshing} label="重新读取" pendingLabel="读取中…" onClick={reload} />
    <div className={styles.createControl}><button type="button" className="module-btn primary" onClick={() => setCreateOpen((value) => !value)}><Plus size={15} />新建记忆</button>{createOpen && <div className={styles.createPopover}><div className={styles.popoverHead}><strong>新增长期记忆</strong><button type="button" aria-label="关闭新建记忆" onClick={() => setCreateOpen(false)}><X size={15} /></button></div><p>新建记忆只能保存至长期记忆栏。</p><textarea value={newContent} maxLength={10000} placeholder="请输入要长期保存的记忆内容……" onChange={(event) => setNewContent(event.target.value)} /><small>{newContent.length} / 10000</small><div className={styles.popoverActions}><button type="button" className="module-btn" onClick={() => { setCreateOpen(false); setNewContent('') }}>取消</button><button type="button" className="module-btn primary" disabled={!newContent.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存</button></div></div>}</div>
  </div>}>
    {summary.isError && <ModuleError message="记忆读取失败，请检查运行服务状态。" />}
    <div className={styles.statGrid}>{TABS.map((tier) => <StatCard key={tier} tier={tier} count={counts[tier]} active={activeTier === tier} onClick={() => setActiveTier(tier)} />)}</div>
    <div className={styles.tabsBar}>{TABS.map((tier) => <button type="button" key={tier} className={activeTier === tier ? styles.tabActive : ''} onClick={() => setActiveTier(tier)}>{TIER_LABELS[tier]}</button>)}</div>
    <div className={`${styles.workspace} ${activeTier === 'important' ? styles.workspaceSingle : ''}`}>
      {activeTier !== 'important' && <section className={styles.listPanel}><div className={styles.panelHeading}><div><strong>{TIER_LABELS[activeTier]}</strong><span>{WEIGHTED_TIERS.has(activeTier) ? '编辑后权重 +1，每天最多增加一次。' : '手动创建并长期保存的稳定记忆。'}</span></div><span className={styles.panelCount}>{visibleRows.length}</span></div><div className={styles.toolbar}><label className={styles.search}><Search size={15} /><input value={queryText} placeholder="搜索当前记忆栏……" onChange={(event) => setQueryText(event.target.value)} /></label><select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="newest">按更新时间</option><option value="oldest">按最早时间</option>{WEIGHTED_TIERS.has(activeTier) && <option value="weight">按权重排序</option>}</select></div><div className={styles.rows}>{pagedRows.length ? pagedRows.map((row) => <article key={row.key} className={`${styles.row} ${selectedKey === row.key ? styles.rowSelected : ''}`} onClick={() => setSelectedKey(row.key)}><span className={styles.rowDot} /><div className={styles.rowCopy}><strong>{row.title}</strong><span>{row.preview || row.filename}</span></div><div className={styles.rowMeta}>{WEIGHTED_TIERS.has(row.tier) && <span className={styles.weight}>权重 {row.weight}</span>}<small>{formatUpdated(row.updatedAt)}</small><button type="button" onClick={(event) => { event.stopPropagation(); setSelectedKey(row.key) }}>编辑</button><button type="button" onClick={(event) => { event.stopPropagation(); setPendingDelete(row) }}>删除</button></div></article>) : <EmptyPanel title="当前记忆栏暂无内容" description="可以切换其他记忆栏或重新读取数据。" icon={<Brain size={22} />} />}</div><div className={styles.pagination}><div><button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={14} /></button><b>{page} / {totalPages}</b><button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}><ChevronRight size={14} /></button></div></div></section>}
      <aside className={styles.editorPanel}><div className={styles.editorHeading}><div><strong>编辑查看</strong><span>{selected ? selected.title : activeTier === 'important' ? '临时重要记忆文件' : '选择一条记忆'}</span></div>{selected && activeTier !== 'important' && <button type="button" aria-label="关闭编辑查看" onClick={() => setSelectedKey('')}><X size={16} /></button>}</div>{!selected ? <div className={styles.editorEmpty}><Brain size={42} /><strong>{activeTier === 'important' ? '临时重要记忆文件不可用' : '选择一条记忆'}</strong><span>{activeTier === 'important' ? '请重新读取数据或检查记忆文件状态。' : '点击左侧记忆后在此处查看和编辑。'}</span></div> : <><div className={styles.metadata}><span>记忆类型：<b>{TIER_LABELS[selected.tier]}</b></span><span>创建时间：<b>{formatUpdated(selected.createdAt)}</b></span><span>内容更新：<b>{formatUpdated(selected.updatedAt)}</b></span>{selected.lastUsedAt && <span>最近使用：<b>{formatUpdated(selected.lastUsedAt)}</b></span>}{WEIGHTED_TIERS.has(selected.tier) && <span>当前权重：<b>{item.data?.weight ?? selected.weight}</b></span>}</div>{WEIGHTED_TIERS.has(selected.tier) && <div className={styles.weightNotice}><Brain size={14} />{weightToday ? '该记忆今天已经因编辑增加过权重，再次保存不会继续增加。' : '保存编辑后权重 +1，同一条记忆每天最多增加一次。'}</div>}<div className={styles.editorToolbar}><button type="button" className={previewing ? styles.previewActive : ''} onClick={() => setPreviewing((value) => !value)}>{previewing ? <><FileText size={14} />返回编辑</> : <><Eye size={14} />Markdown 预览</>}</button>{changed && <small>有未保存修改</small>}</div><div className={styles.editorBody}>{loadingDetail ? <div className={styles.editorLoading}>正在读取记忆内容…</div> : previewing ? <article className={styles.markdown}><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown></article> : <textarea value={draft} spellCheck={false} placeholder="输入记忆内容……" onChange={(event) => setDraft(event.target.value)} />}</div><div className={styles.editorActions}><button type="button" className="module-btn" disabled={!changed || save.isPending} onClick={() => setDraft(original)}><RotateCcw size={14} />恢复</button>{selected.tier !== 'important' && <button type="button" className="module-btn danger" disabled={save.isPending || remove.isPending} onClick={() => setPendingDelete(selected)}><Trash2 size={14} />删除此记忆</button>}<button type="button" className="module-btn primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>{save.isPending ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存编辑</button></div>{save.isError && <ModuleError message={String(save.error)} />}</>}</aside>
    </div>
    {pendingDelete && <div className={styles.deleteOverlay}><div className={styles.deleteDialog}><Trash2 size={25} /><strong>确认删除这条记忆？</strong><span>“{pendingDelete.title}”删除后无法直接恢复。</span><div><button type="button" className="module-btn" onClick={() => setPendingDelete(null)}>取消</button><button type="button" className="module-btn danger" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}确认删除</button></div></div></div>}
  </ModuleFrame>
}
