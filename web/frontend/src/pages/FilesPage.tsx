import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  File,
  Folder,
  FolderOpen,
  HardDrive,
  RefreshCw,
  Pencil,
  Plus,
  Save,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import {
  deleteTmpFile,
  deleteUserFile,
  createTmpDirectory,
  createUserDirectory,
  getTmpFiles,
  getTmpText,
  getUserFileDownloadUrl,
  getUserFileText,
  getUserFiles,
  moveTmpFile,
  moveUserFile,
  writeTmpText,
  writeUserFileText,
  uploadTmpFile,
  uploadUserFile,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import {
  EmptyPanel,
  formatBytes,
  formatDateTime,
  MetricCard,
  ModuleError,
  ModuleFrame,
} from '../components/ModuleUi'
import type { FileTreeNode } from '../types/api'
import styles from './FilesPage.module.css'

type FileArea = 'file_upload' | 'download' | 'tmp'

interface FlatTreeNode {
  node: FileTreeNode
  depth: number
}

const areaLabels: Record<FileArea, { label: string; detail: string }> = {
  file_upload: { label: '用户上传', detail: 'users/<user>/file_upload' },
  download: { label: '智能体产物', detail: 'users/<user>/download' },
  tmp: { label: '全局临时', detail: 'tmp' },
}

const editableExtensions = new Set(['.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv', '.log', '.py', '.js', '.ts', '.tsx', '.css', '.html'])

function flattenTree(nodes: FileTreeNode[], depth = 0): FlatTreeNode[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(node.type === 'directory' ? flattenTree(node.children, depth + 1) : []),
  ])
}

export function FilesPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [area, setArea] = useState<FileArea>('file_upload')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [editPath, setEditPath] = useState('')
  const [editDraft, setEditDraft] = useState('')
  const userScope = area === 'tmp' ? 'file_upload' : area
  const userFilesQuery = useQuery({
    queryKey: ['user-files', user, userScope],
    queryFn: () => getUserFiles(user, userScope),
    enabled: Boolean(user) && area !== 'tmp',
  })
  const tmpFilesQuery = useQuery({
    queryKey: ['tmp-files'],
    queryFn: getTmpFiles,
    enabled: area === 'tmp',
  })
  const activeQuery = area === 'tmp' ? tmpFilesQuery : userFilesQuery
  const textQuery = useQuery({ queryKey: ['file-text', user, area, editPath], queryFn: () => area === 'tmp' ? getTmpText(editPath) : getUserFileText(user, area, editPath), enabled: Boolean(editPath) })
  useEffect(() => { if (textQuery.data) setEditDraft(textQuery.data.content) }, [textQuery.data])
  const data = activeQuery.data
  const rows = useMemo(() => flattenTree(data?.tree || []), [data?.tree])

  const deleteMutation = useMutation({
    mutationFn: (path: string) => (
      area === 'tmp' ? deleteTmpFile(path) : deleteUserFile(user, area, path)
    ),
    onSuccess: async (result) => {
      setPendingDelete(null)
      setNotice(`已删除 ${result.path}`)
      if (area === 'tmp') await queryClient.invalidateQueries({ queryKey: ['tmp-files'] })
      else await queryClient.invalidateQueries({ queryKey: ['user-files', user, area] })
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : '文件删除失败'),
  })
  const refreshFiles = async () => {
    if (area === 'tmp') await queryClient.invalidateQueries({ queryKey: ['tmp-files'] })
    else await queryClient.invalidateQueries({ queryKey: ['user-files', user, area] })
  }
  const uploadMutation = useMutation({
    mutationFn: ({ path, file }: { path: string; file: File }) => area === 'tmp' ? uploadTmpFile(path, file) : uploadUserFile(user, area, path, file),
    onSuccess: async () => { setNotice('文件已上传'); await refreshFiles() },
    onError: (error) => setNotice(error instanceof Error ? error.message : '上传失败'),
  })
  const directoryMutation = useMutation({
    mutationFn: (path: string) => area === 'tmp' ? createTmpDirectory(path) : createUserDirectory(user, area, path),
    onSuccess: async () => { setNotice('目录已创建'); await refreshFiles() },
    onError: (error) => setNotice(error instanceof Error ? error.message : '目录创建失败'),
  })
  const moveMutation = useMutation({
    mutationFn: ({ path, newPath }: { path: string; newPath: string }) => area === 'tmp' ? moveTmpFile(path, newPath) : moveUserFile(user, area, path, newPath),
    onSuccess: async () => { setNotice('路径已更新'); await refreshFiles() },
    onError: (error) => setNotice(error instanceof Error ? error.message : '重命名失败'),
  })
  const textMutation = useMutation({
    mutationFn: () => area === 'tmp' ? writeTmpText(editPath, editDraft) : writeUserFileText(user, area, editPath, editDraft),
    onSuccess: async () => { setNotice('文本已保存'); await refreshFiles(); await textQuery.refetch() },
    onError: (error) => setNotice(error instanceof Error ? error.message : '文本保存失败'),
  })

  const switchArea = (next: FileArea) => {
    setArea(next)
    setPendingDelete(null)
    setEditPath('')
    setNotice('')
  }

  return (
    <ModuleFrame
      kicker="Files & Artifacts"
      title="文件空间"
      description="浏览和管理用户上传、智能体生成产物与全局临时文件；所有路径操作都限制在对应目录内。"
      actions={<><input ref={fileInput} type="file" hidden onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; const path = window.prompt('保存路径', file.name)?.trim(); if (path) uploadMutation.mutate({ path, file }); event.currentTarget.value = '' }} /><button className="module-btn" onClick={() => void activeQuery.refetch()}><RefreshCw size={15} />刷新文件树</button><button className="module-btn" onClick={() => { const path = window.prompt('新目录路径', '')?.trim(); if (path) directoryMutation.mutate(path) }}><Plus size={15} />新建目录</button><button className="module-btn primary" onClick={() => fileInput.current?.click()}><UploadCloud size={15} />上传文件</button></>}
    >
      {activeQuery.isError && <ModuleError message="文件树读取失败，请检查目录状态或重新登录。" />}
      <div className={styles.areaTabs} role="tablist" aria-label="文件区域">
        {(Object.keys(areaLabels) as FileArea[]).map((value) => {
          const Icon = value === 'file_upload' ? UploadCloud : value === 'download' ? Download : HardDrive
          return <button key={value} role="tab" aria-selected={area === value} className={area === value ? styles.active : ''} onClick={() => switchArea(value)}><Icon size={17} /><span><strong>{areaLabels[value].label}</strong><small>{areaLabels[value].detail.replace('<user>', user || '—')}</small></span></button>
        })}
      </div>

      <section className="metric-strip">
        <MetricCard label="普通文件" value={data?.summary.total_files ?? '—'} detail="可下载或删除" symbol={<File size={16} />} />
        <MetricCard label="目录" value={data?.summary.total_dirs ?? '—'} detail="只读结构" symbol={<Folder size={16} />} />
        <MetricCard label="占用空间" value={data ? formatBytes(data.summary.total_size) : '—'} detail={data?.root || '正在读取'} symbol={<HardDrive size={16} />} />
        <MetricCard label="安全边界" value="目录内" detail="过滤隐藏项、缓存和链接" symbol={<FolderOpen size={16} />} tone="success" />
      </section>

      {notice && <div className={styles.notice} role="status">{notice}<button type="button" onClick={() => setNotice('')}>×</button></div>}
      {pendingDelete && <div className={styles.confirmBar} role="alert">
        <span><strong>确认删除这个文件？</strong><small>{pendingDelete} · 删除后无法通过 Web 恢复</small></span>
        <span><button type="button" onClick={() => setPendingDelete(null)}>取消</button><button type="button" className={styles.dangerButton} disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(pendingDelete)}>{deleteMutation.isPending ? '正在删除…' : '确认删除'}</button></span>
      </div>}

      {editPath && <article className={`panel ${styles.filePanel}`}>
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><Pencil size={15} /></span><span><strong>文本编辑</strong><span>{editPath}</span></span></div><button className="module-btn" onClick={() => setEditPath('')}>关闭</button></div>
        <textarea className="config-json-editor" value={editDraft} onChange={(event) => setEditDraft(event.target.value)} spellCheck={false} />
        <div className="module-actions"><button className="module-btn primary" onClick={() => textMutation.mutate()} disabled={textMutation.isPending}><Save size={14} />保存文本</button></div>
      </article>}

      <article className={`panel ${styles.filePanel}`}>
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">F</span><span><strong>{areaLabels[area].label}</strong><span>{data?.root || areaLabels[area].detail.replace('<user>', user || '—')}</span></span></div><span className="panel-count">{rows.length}</span></div>
        {activeQuery.isLoading ? <div className={styles.loading}>正在读取文件树…</div> : rows.length ? <div className={styles.fileList}>
          {rows.map(({ node, depth }) => <div className={`${styles.fileRow} ${node.type === 'directory' ? styles.directory : ''}`} key={`${node.type}:${node.relative_path}`}>
            <span className={styles.fileIdentity} style={{ paddingLeft: `${12 + depth * 22}px` }}>
              <span className={styles.fileIcon}>{node.type === 'directory' ? <Folder size={16} /> : <File size={16} />}</span>
              <span><strong>{node.name}</strong><small>{node.relative_path}</small></span>
            </span>
            <span className={styles.fileMeta}>{node.type === 'file' ? <><b>{formatBytes(node.size)}</b><small>{formatDateTime(node.updated_at)}</small></> : <small>{node.children.length} 个直接子项</small>}</span>
            <span className={styles.fileActions}>
              {node.type === 'file' && area !== 'tmp' ? <a href={getUserFileDownloadUrl(user, area, node.relative_path)} download={node.name} title="下载文件"><Download size={15} /><span>下载</span></a> : null}
              {node.type === 'file' && editableExtensions.has(node.extension.toLowerCase()) ? <button type="button" aria-label="编辑文本" onClick={() => setEditPath(node.relative_path)} title="编辑文本"><Pencil size={15} /><span>编辑</span></button> : null}
              <button type="button" aria-label="重命名或移动" onClick={() => { const next = window.prompt('新的相对路径', node.relative_path)?.trim(); if (next && next !== node.relative_path) moveMutation.mutate({ path: node.relative_path, newPath: next }) }} title="重命名或移动"><Pencil size={15} /><span>重命名</span></button>
              {node.type === 'file' ? <button type="button" aria-label="删除文件" onClick={() => setPendingDelete(node.relative_path)} title="删除文件"><Trash2 size={15} /><span>删除</span></button> : null}
            </span>
          </div>)}
        </div> : <EmptyPanel title="当前区域没有文件" description="目录不存在或尚未产生可展示的普通文件。" icon={<FolderOpen size={21} />} />}
      </article>
    </ModuleFrame>
  )
}
