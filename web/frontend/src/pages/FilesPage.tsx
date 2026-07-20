import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  File,
  Folder,
  FolderOpen,
  HardDrive,
  RefreshCw,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import {
  deleteTmpFile,
  deleteUserFile,
  getTmpFiles,
  getUserFileDownloadUrl,
  getUserFiles,
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

function flattenTree(nodes: FileTreeNode[], depth = 0): FlatTreeNode[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(node.type === 'directory' ? flattenTree(node.children, depth + 1) : []),
  ])
}

export function FilesPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [area, setArea] = useState<FileArea>('file_upload')
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
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

  const switchArea = (next: FileArea) => {
    setArea(next)
    setPendingDelete(null)
    setNotice('')
  }

  return (
    <ModuleFrame
      kicker="Files & Artifacts"
      title="文件空间"
      description="浏览用户上传、智能体生成产物和全局临时文件。当前接口只开放普通文件的下载与删除。"
      actions={<button className="module-btn" onClick={() => void activeQuery.refetch()}><RefreshCw size={15} />刷新文件树</button>}
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
              {node.type === 'file' ? <button type="button" aria-label="删除文件" onClick={() => setPendingDelete(node.relative_path)} title="删除文件"><Trash2 size={15} /><span>删除</span></button> : <span className={styles.readonly}>目录只读</span>}
            </span>
          </div>)}
        </div> : <EmptyPanel title="当前区域没有文件" description="目录不存在或尚未产生可展示的普通文件。" icon={<FolderOpen size={21} />} />}
      </article>
    </ModuleFrame>
  )
}
