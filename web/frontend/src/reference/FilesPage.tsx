import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import * as api from '../api/client'
import type { ShellOutletContext } from '../components/appShellTypes'
import type { FileListEntry, FileSortBy, FileSortOrder } from '../types/api'
import { GlobalConfirmDialog } from '../components/GlobalConfirmDialog'
import { FilePreview, fileKind as kind, type FileArea } from './FilePreview'
import { formatBytes, formatDateTime } from '../components/ModuleUi'
import { copyText } from '../utils/clipboard'
import { ReferenceIcon } from './ReferenceIcon'
import { toast } from './runtime.js'

type Area = FileArea
const areas: Array<{ key: Area; label: string }> = [
  { key: 'file_upload', label: '用户上传' },
  { key: 'download', label: '智能体产物' },
  { key: 'tmp', label: '全局临时区' },
]
/** 样例的文件空间布局；排序、搜索、目录和分页均交给后端，不用静态数组模拟。 */
export function FilesPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const client = useQueryClient()
  const [area, setArea] = useState<Area>('file_upload')
  const [path, setPath] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [sortBy, setSortBy] = useState<FileSortBy>('name')
  const [sortOrder, setSortOrder] = useState<FileSortOrder>('asc')
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState<FileListEntry | null>(null)
  const [rename, setRename] = useState<FileListEntry | null>(null)
  const [newName, setNewName] = useState('')
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const fileQuery = useQuery({
    queryKey: ['reference-files', user, area, path, search, page, sortBy, sortOrder],
    queryFn: () => area === 'tmp' ? api.getTmpFiles(path, search, page, 6, sortBy, sortOrder) : api.getUserFiles(user, area, path, search, page, 6, sortBy, sortOrder),
    enabled: Boolean(user),
  })
  useEffect(() => {
    setPath(''); setSearch(''); setPage(1); setSelected([]); setPreview(null); setRename(null); setConfirm(null)
  }, [user])
  const mutation = useMutation({
    mutationFn: async (operation: () => Promise<unknown>) => operation(),
    onSuccess: async () => {
      setError(''); setRename(null); setConfirm(null); setSelected([]); setPreview(null)
      await client.invalidateQueries({ queryKey: ['reference-files', user] })
      toast('文件操作已完成', { kind: 'success' })
    },
    onError: cause => setError(cause instanceof Error ? cause.message : '文件操作失败'),
  })
  const goPath = (value: string) => { setPath(value); setSearch(''); setPage(1); setSelected([]); setPreview(null) }
  const entries = fileQuery.data?.entries ?? []
  const allSelected = entries.length > 0 && entries.every(file => selected.includes(file.relative_path))
  const downloadUrl = (file: FileListEntry) => area === 'tmp' ? api.getTmpFileDownloadUrl(file.relative_path) : api.getUserFileDownloadUrl(user, area, file.relative_path)
  const doDelete = () => {
    mutation.mutate(() => confirm === 'all'
      ? area === 'tmp' ? api.deleteAllTmpFiles() : api.deleteAllUserFiles(user, area)
      : area === 'tmp' ? api.deleteTmpFiles(selected) : api.deleteUserFiles(user, area, selected))
  }
  const upload = (files: FileList) => {
    if (area === 'tmp') { setError('全局临时区没有网页上传接口，请选择用户上传或智能体产物。'); return }
    const list = Array.from(files)
    if (list.some(file => file.size > 80 * 1024 * 1024)) { setError('每个文件不能超过 80 MiB。'); return }
    mutation.mutate(async () => { for (const file of list) await api.uploadUserFile(user, area, path, file) })
  }
  const submitRename = () => {
    if (!rename || area === 'tmp') return
    if (!newName.trim() || /[\\/:*?"<>|]/.test(newName)) { setError('名称不能为空，且不能包含路径分隔符或 Windows 非法字符。'); return }
    const parent = rename.parent_path || rename.relative_path.split('/').slice(0, -1).join('/')
    mutation.mutate(() => api.moveUserFile(user, area, rename.relative_path, parent ? `${parent}/${newName.trim()}` : newName.trim()))
  }
  return <div className="page file-space-page"><div className="page-inner wide">
    <div className="page-head"><div><div className="page-title">文件空间</div><div className="page-desc">三个区域的影响范围不同：用户上传与产物按用户隔离，全局临时区不隔离。列表由后端排序分页。</div></div>
      <div className="page-head-actions"><button className="btn btn-outline" onClick={() => { setPreview(null); void fileQuery.refetch() }} disabled={fileQuery.isFetching}><ReferenceIcon name="refresh" small />刷新</button>
        <button className="btn btn-primary" onClick={() => inputRef.current?.click()} disabled={area === 'tmp' || mutation.isPending}><ReferenceIcon name="upload" small />上传到当前目录</button></div></div>
    <input ref={inputRef} type="file" multiple hidden aria-label="上传文件" onChange={event => { if (event.target.files?.length) upload(event.target.files); event.currentTarget.value = '' }} />
    <div className="tabs">{areas.map(item => <button key={item.key} className={`tab ${area === item.key ? 'is-active' : ''}`} onClick={() => { setArea(item.key); goPath(''); setSearch('') }}>{item.label}</button>)}</div>
    <div className="split files-split"><div className="file-list-column">
    <div className="toolbar file-toolbar"><div className="search-box"><ReferenceIcon name="search" small /><input className="input" type="search" aria-label="搜索当前目录" placeholder="按名称搜索当前目录…" value={search} onChange={event => { setPreview(null); setSearch(event.target.value); setPage(1); setSelected([]) }} /></div>
      <select className="select reference-auto-width" aria-label="文件排序" value={sortBy} onChange={event => { setPreview(null); setSortBy(event.target.value as FileSortBy); setPage(1) }}><option value="name">按名称</option><option value="updated_at">按更新时间</option><option value="size">按大小</option></select>
      <button className="btn btn-outline btn-sm" onClick={() => { setPreview(null); setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc') }}><ReferenceIcon name={sortOrder === 'asc' ? 'arrowUp' : 'arrowDown'} small />{sortOrder === 'asc' ? '升序' : '降序'}</button><div className="spacer" /><span className="faint">{fileQuery.data?.summary.total_files ?? '—'} 文件 · {formatBytes(fileQuery.data?.summary.total_size ?? 0)}</span>
    </div>
    <div className="file-breadcrumb"><button className="file-crumb" onClick={() => goPath('')}><ReferenceIcon name="folderOpen" small /></button>
      {path.split('/').filter(Boolean).map((segment, index, segments) => <span key={index}> / <button className="file-crumb" onClick={() => goPath(segments.slice(0, index + 1).join('/'))}>{segment}</button></span>)}</div>
    {error && <div className="banner banner-danger" role="alert">{error}<button className="icon-btn" onClick={() => setError('')} aria-label="关闭错误">×</button></div>}
    {fileQuery.isError && <div className="banner banner-danger" role="alert">{fileQuery.error instanceof Error ? fileQuery.error.message : '目录读取失败'}</div>}
    {selected.length > 0 && <div className="selection-bar"><ReferenceIcon name="checkCircle" small /><strong>已选择 {selected.length} 项</strong><div className="spacer" />
      <button className="btn btn-ghost btn-sm" onClick={() => setSelected([])}>取消选择</button><button className="btn btn-danger btn-sm" onClick={() => setConfirm('selected')}>删除选中</button></div>}
    <div className="card reference-file-card"><div className="card-head"><label className="reference-check-label"><input type="checkbox" aria-label="选择当前页全部文件" checked={allSelected} onChange={() => setSelected(allSelected ? [] : entries.map(file => file.relative_path))} /><strong>当前目录</strong><span className="section-count">{fileQuery.data?.pagination.total_items ?? '—'} 项</span></label>
      <button className="btn btn-ghost btn-sm" onClick={() => setConfirm('all')} disabled={mutation.isPending}><ReferenceIcon name="trash" small />清空空间</button></div>
      {fileQuery.isLoading ? <div className="reference-loading"><span className="spinner" />正在读取目录…</div> : entries.length === 0 && !fileQuery.isError ? <div className="empty"><ReferenceIcon name="folder" /><div className="empty-title">{search ? '没有匹配的文件' : '当前目录是空的'}</div></div> : <div className="list reference-file-list">
        {entries.map(file => <div className={`list-row ${selected.includes(file.relative_path) || preview?.relative_path === file.relative_path ? 'is-active' : ''}`} key={file.relative_path}>
          <input type="checkbox" aria-label={`选择 ${file.name}`} checked={selected.includes(file.relative_path)} onChange={() => setSelected(current => current.includes(file.relative_path) ? current.filter(value => value !== file.relative_path) : [...current, file.relative_path])} />
          <button className="reference-file-main" onClick={() => file.type === 'directory' ? goPath(file.relative_path) : setPreview(file)}>
            <div className={`file-icon ${file.type === 'directory' ? 'is-dir' : `is-${kind(file)}`}`}><ReferenceIcon name={file.type === 'directory' ? 'folder' : ['text', 'document'].includes(kind(file)) ? 'fileText' : kind(file)} small /></div>
            <div className="reference-entry-copy"><div className="file-name">{file.name}</div><div className="file-path">{file.type === 'directory' ? `${file.child_count} 个子项` : file.relative_path}</div></div>
          </button>
          <span className="reference-file-size">{file.type === 'directory' ? '—' : formatBytes(file.size)}</span><span className="reference-file-time">{formatDateTime(file.updated_at)}</span>
          <div className="reference-file-actions">
            {file.type === 'file' && <a className="icon-btn" href={downloadUrl(file)} download={file.name} aria-label={`下载 ${file.name}`}><ReferenceIcon name="download" small /></a>}
            <button className="icon-btn" disabled={area === 'tmp' || file.type === 'directory'} aria-label={`重命名 ${file.name}`} onClick={() => { setRename(file); setNewName(file.name); setError('') }}><ReferenceIcon name="edit" small /></button>
            <button className="icon-btn" aria-label={`复制 ${file.name} 路径`} onClick={() => void copyText(file.relative_path).then(() => toast('已复制相对路径', { kind: 'success' })).catch(() => setError('复制失败'))}><ReferenceIcon name="copy" small /></button>
          </div>
        </div>)}
      </div>}
    </div>
    {(fileQuery.data?.pagination.total_pages ?? 1) > 1 && <div className="pager"><button disabled={!fileQuery.data?.pagination.has_previous} onClick={() => { setPreview(null); setPage(page - 1); setSelected([]) }}>上一页</button><span>{page} / {fileQuery.data?.pagination.total_pages}</span><button disabled={!fileQuery.data?.pagination.has_next} onClick={() => { setPreview(null); setPage(page + 1); setSelected([]) }}>下一页</button></div>}
    </div><aside className="file-detail-column">{preview
      ? <FilePreview key={`${user}:${area}:${preview.relative_path}`} file={preview} user={user} area={area} onClose={() => setPreview(null)} />
      : <section className="editor-panel file-detail-panel"><div className="editor-head"><div className="editor-title">文件详情</div></div>
        <div className="file-detail-body"><div className="empty"><ReferenceIcon name="fileText" /><div className="empty-title">选择一个文件</div>
          <div className="empty-desc">在左侧选择文件查看内容，可点击放大预览。</div></div></div></section>}
    </aside></div>
    {rename && <div className="overlay overlay-center" role="dialog" aria-modal="true" aria-label="重命名文件"><div className="modal modal-sm"><div className="modal-head"><div className="modal-title">重命名文件</div><button className="icon-btn" onClick={() => setRename(null)} aria-label="关闭重命名">×</button></div><div className="modal-body"><label className="field"><span className="field-label">新名称</span><input className="input" value={newName} autoFocus onChange={event => setNewName(event.target.value)} /></label>{error && <div className="banner banner-danger">{error}</div>}</div><div className="modal-foot"><button className="btn btn-ghost" onClick={() => setRename(null)}>取消</button><button className="btn btn-primary" disabled={mutation.isPending} onClick={submitRename}>{mutation.isPending ? '重命名中…' : '重命名'}</button></div></div></div>}
    <GlobalConfirmDialog open={Boolean(confirm)} title={confirm === 'all' ? area === 'tmp' ? '清空全局临时区' : '清空当前区域' : '删除选中的文件'}
      description={area === 'tmp' ? '临时区不按用户隔离，删除会影响所有用户可见的文件。此操作不可撤销。' : '文件和子目录会被永久删除，此操作不可撤销。'}
      detail={confirm === 'selected' ? selected.join('\n') : fileQuery.data?.root} error={error} icon={<ReferenceIcon name="alertTriangle" />} tone="danger"
      confirmLabel="确认删除" pending={mutation.isPending} pendingLabel="删除中…" onCancel={() => { setConfirm(null); setError('') }} onConfirm={doDelete} />
  </div></div>
}
