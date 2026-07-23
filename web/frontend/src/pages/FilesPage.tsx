import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Braces,
  CheckSquare,
  ChevronRight,
  Clipboard,
  Download,
  File,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  HardDrive,
  Info,
  Pencil,
  Search,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import {
  deleteAllUserFiles,
  deleteAllTmpFiles,
  deleteTmpFiles,
  deleteUserFiles,
  getTmpFiles,
  getUserFileDownloadUrl,
  getUserFiles,
  moveUserFile,
  uploadUserFile,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import {
  EmptyPanel,
  formatBytes,
  formatDateTime,
  ModuleError,
  ModuleFrame,
  RefreshActionButton,
} from '../components/ModuleUi'
import type { FileTreeNode } from '../types/api'
import styles from './FilesPage.module.css'

type FileArea = 'file_upload' | 'download' | 'tmp'
type UserFileArea = Exclude<FileArea, 'tmp'>

interface FileEntry {
  type: 'directory' | 'file'
  name: string
  relativePath: string
  parentPath: string
  extension: string
  size: number
  updatedAt: number
  childCount: number
}

interface DeleteRequest {
  kind: 'single' | 'selected' | 'all'
  paths: string[]
  label: string
}

const areaLabels: Record<FileArea, { label: string; detail: string; description: string }> = {
  file_upload: {
    label: '用户上传',
    detail: 'users/<user>/file_upload',
    description: '由当前用户上传并交给智能体使用的文件',
  },
  download: {
    label: '智能体产物',
    detail: 'users/<user>/download',
    description: '智能体为当前用户生成的可下载产物',
  },
  tmp: {
    label: '全局临时',
    detail: 'tmp',
    description: '运行过程产生的全局临时文件',
  },
}

const imageExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'])
const archiveExtensions = new Set(['.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz'])
const codeExtensions = new Set(['.js', '.jsx', '.ts', '.tsx', '.py', '.json', '.yaml', '.yml', '.css', '.scss', '.html', '.xml', '.sh', '.ps1'])
const documentExtensions = new Set(['.md', '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.csv', '.log'])

function parentPathOf(path: string): string {
  const slash = path.lastIndexOf('/')
  return slash < 0 ? '' : path.slice(0, slash)
}

function flattenTree(nodes: FileTreeNode[]): FileEntry[] {
  const entries: FileEntry[] = []

  const visit = (node: FileTreeNode): { size: number; updatedAt: number } => {
    if (node.type === 'file') {
      entries.push({
        type: 'file',
        name: node.name,
        relativePath: node.relative_path,
        parentPath: parentPathOf(node.relative_path),
        extension: node.extension,
        size: node.size,
        updatedAt: node.updated_at,
        childCount: 0,
      })
      return { size: node.size, updatedAt: node.updated_at }
    }

    const children = node.children.map(visit)
    const aggregate = {
      size: children.reduce((sum, child) => sum + child.size, 0),
      updatedAt: children.reduce((latest, child) => Math.max(latest, child.updatedAt), 0),
    }
    entries.push({
      type: 'directory',
      name: node.name,
      relativePath: node.relative_path,
      parentPath: parentPathOf(node.relative_path),
      extension: '',
      size: aggregate.size,
      updatedAt: aggregate.updatedAt,
      childCount: node.children.length,
    })
    return aggregate
  }

  nodes.forEach(visit)
  return entries
}

function fileKind(entry: FileEntry): { label: string; className: string } {
  if (entry.type === 'directory') return { label: '文件夹', className: styles.kindFolder }
  const extension = entry.extension.toLowerCase()
  if (imageExtensions.has(extension)) return { label: '图片', className: styles.kindImage }
  if (archiveExtensions.has(extension)) return { label: '压缩包', className: styles.kindArchive }
  if (codeExtensions.has(extension)) return { label: '代码', className: styles.kindCode }
  if (documentExtensions.has(extension)) return { label: '文档', className: styles.kindDocument }
  return { label: extension ? extension.slice(1).toUpperCase() : '文件', className: styles.kindOther }
}

function EntryIcon({ entry, size = 18 }: { entry: FileEntry; size?: number }) {
  if (entry.type === 'directory') return <Folder size={size} />
  const extension = entry.extension.toLowerCase()
  if (imageExtensions.has(extension)) return <FileImage size={size} />
  if (archiveExtensions.has(extension)) return <Archive size={size} />
  if (codeExtensions.has(extension)) return <Braces size={size} />
  if (documentExtensions.has(extension)) return <FileText size={size} />
  return <File size={size} />
}

function joinPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name
}

export function FilesPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [area, setArea] = useState<FileArea>('file_upload')
  const [currentPath, setCurrentPath] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedPath, setSelectedPath] = useState('')
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set())
  const [notice, setNotice] = useState('')
  const [deleteRequest, setDeleteRequest] = useState<DeleteRequest | null>(null)
  const [renameTarget, setRenameTarget] = useState<FileEntry | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const userFilesQuery = useQuery({
    queryKey: ['user-files', user, area],
    queryFn: () => getUserFiles(user, area as UserFileArea),
    enabled: Boolean(user) && area !== 'tmp',
  })
  const tmpFilesQuery = useQuery({
    queryKey: ['tmp-files'],
    queryFn: getTmpFiles,
    enabled: area === 'tmp',
  })
  const activeQuery = area === 'tmp' ? tmpFilesQuery : userFilesQuery
  const data = activeQuery.data
  const allEntries = useMemo(() => flattenTree(data?.tree ?? []), [data?.tree])
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase()
  const visibleEntries = useMemo(() => {
    const filtered = normalizedSearch
      ? allEntries.filter((entry) => `${entry.name} ${entry.relativePath}`.toLocaleLowerCase().includes(normalizedSearch))
      : allEntries.filter((entry) => entry.parentPath === currentPath)
    return [...filtered].sort((left, right) => {
      if (left.type !== right.type) return left.type === 'directory' ? -1 : 1
      return left.name.localeCompare(right.name, 'zh-CN', { numeric: true, sensitivity: 'base' })
    })
  }, [allEntries, currentPath, normalizedSearch])
  const selectedEntry = allEntries.find((entry) => entry.relativePath === selectedPath) ?? null
  const visibleFilePaths = visibleEntries.filter((entry) => entry.type === 'file').map((entry) => entry.relativePath)
  const allVisibleSelected = Boolean(visibleFilePaths.length) && visibleFilePaths.every((path) => selectedPaths.has(path))
  const areaRoot = data?.root || areaLabels[area].detail.replace('<user>', user || '—')

  useEffect(() => {
    if (selectedPath && !selectedEntry) setSelectedPath('')
    if (currentPath && !allEntries.some((entry) => entry.type === 'directory' && entry.relativePath === currentPath)) {
      setCurrentPath('')
    }
  }, [allEntries, currentPath, selectedEntry, selectedPath])

  const refreshFiles = async () => {
    if (area === 'tmp') await queryClient.invalidateQueries({ queryKey: ['tmp-files'] })
    else await queryClient.invalidateQueries({ queryKey: ['user-files', user, area] })
    setNotice(`${areaLabels[area].label}文件统计已刷新`)
  }

  const uploadMutation = useMutation({
    mutationFn: async ({ files, directory }: { files: File[]; directory: string }) => {
      await Promise.all(files.map((file) => uploadUserFile(user, 'file_upload', joinPath(directory, file.name), file)))
      return files.length
    },
    onSuccess: async (count) => {
      setNotice(`已上传 ${count} 个文件到用户上传${currentPath ? ` / ${currentPath}` : ''}`)
      await queryClient.invalidateQueries({ queryKey: ['user-files', user, 'file_upload'] })
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : '文件上传失败'),
  })

  const moveMutation = useMutation({
    mutationFn: ({ target, name }: { target: FileEntry; name: string }) => {
      if (area === 'tmp') throw new Error('全局临时区域不允许重命名')
      return moveUserFile(user, area, target.relativePath, joinPath(target.parentPath, name))
    },
    onSuccess: async (_result, variables) => {
      setRenameTarget(null)
      setSelectedPath('')
      setNotice(`已将 ${variables.target.name} 重命名为 ${variables.name}`)
      await queryClient.invalidateQueries({ queryKey: ['user-files', user, area] })
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : '重命名失败'),
  })

  const deleteMutation = useMutation({
    mutationFn: (request: DeleteRequest) => {
      if (area === 'tmp') return request.kind === 'all' ? deleteAllTmpFiles() : deleteTmpFiles(request.paths)
      return request.kind === 'all'
        ? deleteAllUserFiles(user, area)
        : deleteUserFiles(user, area, request.paths)
    },
    onSuccess: async (result) => {
      setDeleteRequest(null)
      setSelectedPath('')
      setSelectedPaths(new Set())
      const areaLabel = areaLabels[area].label
      setNotice(result.deleted_count ? `已删除 ${result.deleted_count} 个${areaLabel}文件` : `${areaLabel}区域没有可删除文件`)
      if (area === 'tmp') await queryClient.invalidateQueries({ queryKey: ['tmp-files'] })
      else await queryClient.invalidateQueries({ queryKey: ['user-files', user, area] })
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : '文件删除失败'),
  })

  const switchArea = (next: FileArea) => {
    setArea(next)
    setCurrentPath('')
    setSearchQuery('')
    setSelectedPath('')
    setSelectedPaths(new Set())
    setDeleteRequest(null)
    setRenameTarget(null)
    setNotice('')
  }

  const enterDirectory = (path: string) => {
    setCurrentPath(path)
    setSearchQuery('')
    setSelectedPath('')
  }

  const copyRelativePath = async (entry: FileEntry) => {
    const path = `${areaRoot}/${entry.relativePath}`
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(path)
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = path
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand?.('copy')
      textarea.remove()
    }
    setNotice(`已复制相对路径：${path}`)
  }

  const openRename = (entry: FileEntry) => {
    setRenameTarget(entry)
    setRenameValue(entry.name)
  }

  const submitRename = () => {
    const name = renameValue.trim()
    if (!renameTarget || !name) return
    if (name.includes('/') || name.includes('\\')) {
      setNotice('新名称不能包含路径分隔符')
      return
    }
    if (name === renameTarget.name) {
      setRenameTarget(null)
      return
    }
    moveMutation.mutate({ target: renameTarget, name })
  }

  const toggleSelection = (path: string) => {
    setSelectedPaths((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const toggleAllVisible = () => {
    setSelectedPaths((current) => {
      const next = new Set(current)
      if (allVisibleSelected) visibleFilePaths.forEach((path) => next.delete(path))
      else visibleFilePaths.forEach((path) => next.add(path))
      return next
    })
  }

  const rootLabel = areaLabels[area].label
  const crumbs = currentPath ? currentPath.split('/') : []
  const operationDescription = area === 'tmp' ? '复制路径、多选删除、全部删除' : '复制路径、重命名、下载、多选删除、全部删除'

  const renderEntryActions = (entry: FileEntry, compact = false) => (
    <>
      <button type="button" onClick={() => void copyRelativePath(entry)} aria-label={`复制相对路径 ${entry.name}`} title="复制相对路径">
        <Clipboard size={14} /><span>{compact ? '复制' : '复制相对路径'}</span>
      </button>
      {area !== 'tmp' && (
        <button type="button" onClick={() => openRename(entry)} aria-label={`重命名 ${entry.name}`} title="重命名">
          <Pencil size={14} /><span>重命名</span>
        </button>
      )}
      {area !== 'tmp' && entry.type === 'file' && (
        <a href={getUserFileDownloadUrl(user, area, entry.relativePath)} download={entry.name} aria-label={`下载 ${entry.name}`} title="下载文件">
          <Download size={14} /><span>下载</span>
        </a>
      )}
      {area === 'tmp' && entry.type === 'file' && (
        <button
          type="button"
          className={styles.deleteAction}
          onClick={() => setDeleteRequest({ kind: 'single', paths: [entry.relativePath], label: entry.name })}
          aria-label={`删除 ${entry.name}`}
          title="删除临时文件"
        >
          <Trash2 size={14} /><span>删除</span>
        </button>
      )}
    </>
  )

  return (
    <ModuleFrame
      kicker="Files & Artifacts"
      title="文件空间"
      description="管理当前用户的上传文件、智能体生成产物与全局临时文件，可逐层进入目录并在当前区域递归搜索。"
      actions={(
        <>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files ?? [])
              if (files.length && area === 'file_upload') uploadMutation.mutate({ files, directory: currentPath })
              event.currentTarget.value = ''
            }}
          />
          {searchOpen ? (
            <label className={styles.headerSearch}>
              <Search size={15} />
              <input
                autoFocus
                value={searchQuery}
                onChange={(event) => { setSearchQuery(event.target.value); setSelectedPath('') }}
                placeholder={`搜索${areaLabels[area].label}`}
                aria-label={`搜索${areaLabels[area].label}`}
              />
              <button type="button" onClick={() => { setSearchOpen(false); setSearchQuery('') }} aria-label="关闭搜索"><X size={14} /></button>
            </label>
          ) : (
            <button className="module-btn" type="button" onClick={() => setSearchOpen(true)}><Search size={15} />在当前区域搜索</button>
          )}
          <RefreshActionButton pending={activeQuery.isFetching} label="刷新文件统计" pendingLabel="刷新中…" onClick={() => { void refreshFiles() }} />
          <button
            className="module-btn primary"
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={area !== 'file_upload' || uploadMutation.isPending}
            title={area === 'file_upload' ? `上传到 ${rootLabel}${currentPath ? ` / ${currentPath}` : ''}` : '上传文件只能进入用户上传区域'}
          >
            <UploadCloud size={15} />{uploadMutation.isPending ? '正在上传…' : '上传到【用户上传】'}
          </button>
        </>
      )}
    >
      {activeQuery.isError && <ModuleError message="文件空间读取失败，请检查目录状态或重新登录。" />}

      <div className={styles.areaTabs} role="tablist" aria-label="文件区域">
        {(Object.keys(areaLabels) as FileArea[]).map((value) => {
          const Icon = value === 'file_upload' ? UploadCloud : value === 'download' ? Download : HardDrive
          return (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={area === value}
              className={area === value ? styles.active : ''}
              onClick={() => switchArea(value)}
            >
              <span className={styles.areaIcon}><Icon size={21} /></span>
              <span className={styles.areaText}>
                <strong>{areaLabels[value].label}</strong>
                <small>{areaLabels[value].detail.replace('<user>', user || '—')}</small>
                <em>{areaLabels[value].description}</em>
              </span>
              {area === value && <span className={styles.currentBadge}>当前区域</span>}
            </button>
          )
        })}
      </div>

      <section className={styles.summaryGrid} aria-label="当前文件区域统计">
        <div><small>文件数</small><strong>{data?.summary.total_files ?? '—'}</strong><span>当前区域全部文件</span></div>
        <div><small>文件夹数</small><strong>{data?.summary.total_dirs ?? '—'}</strong><span>支持逐层进入查看</span></div>
        <div><small>区域占用</small><strong>{data ? formatBytes(data.summary.total_size) : '—'}</strong><span>{areaRoot}</span></div>
        <div className={styles.operationsMetric}><small>可执行操作（当前区域）</small><strong>{operationDescription}</strong><span>操作权限随区域切换</span></div>
        <div><small>安全边界</small><strong>目录内可访问</strong><span>禁止越权访问其他区域</span></div>
      </section>

      {notice && <div className={styles.notice} role="status"><span>{notice}</span><button type="button" onClick={() => setNotice('')} aria-label="关闭提示"><X size={14} /></button></div>}

      <section className={styles.workspaceGrid}>
        <article className={`panel ${styles.browserPanel}`}>
          <div className={styles.browserToolbar}>
            <nav className={styles.breadcrumbs} aria-label="当前目录路径">
              <FolderOpen size={17} />
              <button type="button" onClick={() => enterDirectory('')}>{rootLabel}</button>
              {crumbs.map((crumb, index) => {
                const path = crumbs.slice(0, index + 1).join('/')
                return <span key={path}><ChevronRight size={13} /><button type="button" onClick={() => enterDirectory(path)}>{crumb}</button></span>
              })}
              {normalizedSearch && <span><ChevronRight size={13} /><b>全区域搜索结果</b></span>}
            </nav>
            <div className={styles.tmpToolbar}>
                <span>{selectedPaths.size ? `已选 ${selectedPaths.size} 个文件` : `可多选${areaLabels[area].label}文件`}</span>
                <button
                  type="button"
                  disabled={!selectedPaths.size}
                  onClick={() => setDeleteRequest({ kind: 'selected', paths: [...selectedPaths], label: `${selectedPaths.size} 个已选文件` })}
                >
                  <CheckSquare size={14} />删除已选
                </button>
                <button type="button" className={styles.deleteAction} disabled={!data?.summary.total_files} onClick={() => setDeleteRequest({ kind: 'all', paths: [], label: `${areaLabels[area].label}区域的全部文件` })}>
                  <Trash2 size={14} />全部删除
                </button>
              </div>
          </div>

          <div className={`${styles.fileHeader} ${styles.withCheckbox}`}>
            <input type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} aria-label={`选择当前列表全部${areaLabels[area].label}文件`} disabled={!visibleFilePaths.length} />
            <span>名称与相对路径</span><span>类型</span><span>修改时间</span><span>大小</span><span>操作</span>
          </div>

          {activeQuery.isLoading ? (
            <div className={styles.loading}>正在读取{rootLabel}文件树…</div>
          ) : visibleEntries.length ? (
            <div className={styles.fileList}>
              {visibleEntries.map((entry) => {
                const kind = fileKind(entry)
                const isSelected = selectedPath === entry.relativePath
                const isChecked = selectedPaths.has(entry.relativePath)
                return (
                  <div
                    key={`${entry.type}:${entry.relativePath}`}
                    className={`${styles.fileRow} ${styles.withCheckbox} ${isSelected ? styles.selectedRow : ''}`}
                    onClick={() => setSelectedPath(entry.relativePath)}
                  >
                    {entry.type === 'file'
                      ? <input type="checkbox" checked={isChecked} onClick={(event) => event.stopPropagation()} onChange={() => toggleSelection(entry.relativePath)} aria-label={`选择 ${entry.name}`} />
                      : <span className={styles.checkboxSpacer} />}
                    <button
                      type="button"
                      className={styles.fileIdentity}
                      onClick={(event) => { event.stopPropagation(); entry.type === 'directory' ? enterDirectory(entry.relativePath) : setSelectedPath(entry.relativePath) }}
                      aria-label={entry.type === 'directory' ? `打开目录 ${entry.name}` : `查看文件信息 ${entry.name}`}
                    >
                      <span className={`${styles.fileIcon} ${kind.className}`}><EntryIcon entry={entry} /></span>
                      <span><strong>{entry.name}{entry.type === 'directory' ? '/' : ''}</strong><small>{areaRoot}/{entry.relativePath}</small></span>
                    </button>
                    <span className={`${styles.kindBadge} ${kind.className}`}>{kind.label}</span>
                    <span className={styles.fileMeta}>{entry.updatedAt ? formatDateTime(entry.updatedAt) : '—'}</span>
                    <span className={styles.fileMeta}>{entry.type === 'directory' ? `${entry.childCount} 个直接子项` : formatBytes(entry.size)}</span>
                    <span className={styles.fileActions} onClick={(event) => event.stopPropagation()}>{renderEntryActions(entry, true)}</span>
                  </div>
                )
              })}
            </div>
          ) : (
            <EmptyPanel
              title={normalizedSearch ? `没有找到“${searchQuery.trim()}”` : '当前目录没有文件'}
              description={normalizedSearch ? `搜索范围仅限${rootLabel}，可更换关键词继续查找。` : '当前目录为空，或尚未产生可展示的普通文件。'}
              icon={normalizedSearch ? <Search size={21} /> : <FolderOpen size={21} />}
            />
          )}
          <footer className={styles.browserFooter}>
            <span>{normalizedSearch ? `搜索到 ${visibleEntries.length} 项` : `当前目录 ${visibleEntries.length} 项`}</span>
            <span>区域总计 {data?.summary.total_files ?? 0} 个文件、{data?.summary.total_dirs ?? 0} 个文件夹</span>
          </footer>
        </article>

        <aside className={`panel ${styles.detailPanel}`}>
          <div className={styles.detailHeading}><Info size={16} /><strong>当前选中文件信息</strong></div>
          {selectedEntry ? (
            <div className={styles.detailContent}>
              <div className={`${styles.detailIcon} ${fileKind(selectedEntry).className}`}><EntryIcon entry={selectedEntry} size={24} /></div>
              <h3>{selectedEntry.name}</h3>
              <span className={`${styles.kindBadge} ${fileKind(selectedEntry).className}`}>{fileKind(selectedEntry).label}</span>
              <dl>
                <div><dt>相对路径</dt><dd>{areaRoot}/{selectedEntry.relativePath}</dd></div>
                <div><dt>所属区域</dt><dd>{rootLabel}</dd></div>
                <div><dt>类型</dt><dd>{selectedEntry.type === 'directory' ? '文件夹' : selectedEntry.extension || '普通文件'}</dd></div>
                <div><dt>大小</dt><dd>{selectedEntry.type === 'directory' ? `${formatBytes(selectedEntry.size)}（包含子文件）` : formatBytes(selectedEntry.size)}</dd></div>
                <div><dt>修改时间</dt><dd>{selectedEntry.updatedAt ? formatDateTime(selectedEntry.updatedAt) : '—'}</dd></div>
              </dl>
              <div className={styles.detailActions}>
                <strong>当前区域可操作</strong>
                <div>{renderEntryActions(selectedEntry)}</div>
              </div>
            </div>
          ) : (
            <div className={styles.detailEmpty}>
              <span><Info size={23} /></span>
              <strong>选择一个文件查看详情</strong>
              <p>文件的相对路径、类型、大小、修改时间和当前区域可执行操作会显示在这里。</p>
            </div>
          )}
        </aside>
      </section>

      {typeof document !== 'undefined' && renameTarget ? createPortal(
        <div className={styles.dialogBackdrop} role="presentation" onMouseDown={() => setRenameTarget(null)}>
          <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="rename-title" onMouseDown={(event) => event.stopPropagation()}>
            <div><span className={styles.dialogIcon}><Pencil size={18} /></span><span><strong id="rename-title">重命名{renameTarget.type === 'directory' ? '文件夹' : '文件'}</strong><small>{renameTarget.relativePath}</small></span></div>
            <label>新名称<input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') submitRename() }} /></label>
            <footer><button type="button" onClick={() => setRenameTarget(null)}>取消</button><button type="button" className="module-btn primary" onClick={submitRename} disabled={!renameValue.trim() || moveMutation.isPending}>{moveMutation.isPending ? '正在保存…' : '保存名称'}</button></footer>
          </div>
        </div>,
        document.body,
      ) : null}

      {typeof document !== 'undefined' && deleteRequest ? createPortal(
        <div className={styles.dialogBackdrop} role="presentation" onMouseDown={() => setDeleteRequest(null)}>
          <div className={styles.dialog} role="alertdialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(event) => event.stopPropagation()}>
            <div><span className={`${styles.dialogIcon} ${styles.dangerIcon}`}><Trash2 size={18} /></span><span><strong id="delete-title">确认删除文件</strong><small>{deleteRequest.label}</small></span></div>
            <p>删除后无法通过 Web 恢复。系统会保留当前区域根目录，并自动清理已经变空的子目录。</p>
            <footer><button type="button" onClick={() => setDeleteRequest(null)}>取消</button><button type="button" className={styles.confirmDelete} onClick={() => deleteMutation.mutate(deleteRequest)} disabled={deleteMutation.isPending}>{deleteMutation.isPending ? '正在删除…' : '确认删除'}</button></footer>
          </div>
        </div>,
        document.body,
      ) : null}
    </ModuleFrame>
  )
}
