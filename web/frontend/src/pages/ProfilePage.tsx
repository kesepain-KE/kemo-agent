import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Download, Eye, FilePenLine, Globe2, Pencil, RefreshCw, Save, UserRound } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOutletContext } from 'react-router-dom'
import {
  ApiError,
  getGlobalSoul,
  getUserAvatarUrl,
  getUserSoul,
  updateUserSoul,
  uploadUserAvatar,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { formatBytes, formatDateTime, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import styles from './ProfilePage.module.css'

const MAX_SOUL_CHARS = 65_536
const MAX_AVATAR_BYTES = 5 * 1024 * 1024
const AVATAR_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])

function isNotFound(error: unknown) {
  return error instanceof ApiError && error.status === 404
}

export function ProfilePage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [userDraft, setUserDraft] = useState('')
  const [selectedAvatar, setSelectedAvatar] = useState<File | null>(null)
  const [avatarNotice, setAvatarNotice] = useState('')
  const [avatarFailed, setAvatarFailed] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(() => Date.now())
  const [saveNotice, setSaveNotice] = useState('')

  const userSoulQuery = useQuery({
    queryKey: ['user-soul', user],
    queryFn: () => getUserSoul(user),
    enabled: Boolean(user),
    retry: (count, error) => !isNotFound(error) && count < 1,
  })
  const globalSoulQuery = useQuery({
    queryKey: ['global-soul'],
    queryFn: getGlobalSoul,
    retry: (count, error) => !isNotFound(error) && count < 1,
  })

  useEffect(() => {
    setUserDraft(userSoulQuery.data?.content || '')
  }, [user, userSoulQuery.data?.content])
  useEffect(() => {
    setAvatarFailed(false)
    setAvatarRevision(Date.now())
  }, [user])

  const previewUrl = useMemo(() => selectedAvatar ? URL.createObjectURL(selectedAvatar) : '', [selectedAvatar])
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  const avatarMutation = useMutation({
    mutationFn: (file: File) => uploadUserAvatar(user, file),
    onSuccess: (result) => {
      setAvatarNotice(`头像已更新：${formatBytes(result.size)} · ${result.format}`)
      setSelectedAvatar(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      setAvatarFailed(false)
      setAvatarRevision(Date.now())
    },
    onError: (error) => setAvatarNotice(error instanceof Error ? error.message : '头像上传失败'),
  })
  const userSoulMutation = useMutation({
    mutationFn: (content: string) => updateUserSoul(user, content),
    onSuccess: (result) => {
      queryClient.setQueryData(['user-soul', user], result)
      setSaveNotice('用户人格已原子写入。')
    },
    onError: (error) => setSaveNotice(error instanceof Error ? error.message : '用户人格保存失败'),
  })

  const chooseAvatar = (file: File | undefined) => {
    setAvatarNotice('')
    if (!file) {
      setSelectedAvatar(null)
      return
    }
    if (!AVATAR_TYPES.has(file.type)) {
      setSelectedAvatar(null)
      setAvatarNotice('只支持 PNG、JPEG、GIF 或 WebP 图片。')
      return
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setSelectedAvatar(null)
      setAvatarNotice('头像文件不能超过 5 MB。')
      return
    }
    setSelectedAvatar(file)
  }

  const userMissing = isNotFound(userSoulQuery.error)
  const globalMissing = isNotFound(globalSoulQuery.error)
  const userChanged = userDraft !== (userSoulQuery.data?.content || '')

  return (
    <ModuleFrame
      kicker="Identity & Soul"
      title="身份与人格"
      description="管理当前用户头像和人格 Markdown；全局人格是所有用户共享的安全底线。"
      actions={<button className="module-btn" onClick={() => { void userSoulQuery.refetch(); void globalSoulQuery.refetch(); setAvatarRevision(Date.now()) }}><RefreshCw size={15} />重新读取</button>}
    >
      {((userSoulQuery.isError && !userMissing) || (globalSoulQuery.isError && !globalMissing)) && <ModuleError message="人格文件读取失败，请检查文件编码和访问权限。" />}
      {saveNotice && <div className={styles.notice} role="status">{saveNotice}<button onClick={() => setSaveNotice('')}>×</button></div>}

      <article className={`panel ${styles.profileCard}`}>
        <div className={styles.avatarFrame}>
          {!avatarFailed || previewUrl ? <img src={previewUrl || getUserAvatarUrl(user, avatarRevision)} alt={`${user} 的头像`} onError={() => setAvatarFailed(true)} /> : <span>{user.slice(0, 1).toUpperCase() || 'K'}</span>}
        </div>
        <div className={styles.profileIdentity}><small>当前用户</small><strong>{user || '未选择用户'}</strong><span>users/{user || '—'} · 头像由受保护 API 提供</span></div>
        <div className={styles.avatarControls}>
          <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={(event) => chooseAvatar(event.target.files?.[0])} />
          <button type="button" onClick={() => fileInputRef.current?.click()}><Camera size={15} />选择头像</button>
          <button type="button" className={styles.primaryButton} disabled={!selectedAvatar || avatarMutation.isPending} onClick={() => selectedAvatar && avatarMutation.mutate(selectedAvatar)}>{avatarMutation.isPending ? '正在上传…' : '上传并覆盖'}</button>
          <small>{selectedAvatar ? `${selectedAvatar.name} · ${formatBytes(selectedAvatar.size)}` : 'PNG / JPEG / GIF / WebP，最大 5 MB'}</small>
          {avatarNotice && <span role="status">{avatarNotice}</span>}
        </div>
      </article>

      <div className={styles.editorGrid}>
        <SoulEditor
          icon={<UserRound size={16} />}
          title="用户人格"
          description={`users/${user}/user_soul.md`}
          draft={userDraft}
          onChange={setUserDraft}
          editable
          filename="user_soul.md"
          missing={userMissing}
          updatedAt={userSoulQuery.data?.updated_at}
          saving={userSoulMutation.isPending}
          changed={userChanged}
          onSave={() => userSoulMutation.mutate(userDraft)}
        />
        <SoulEditor
          icon={<Globe2 size={16} />}
          title="全局人格"
          description="config/global_soul.md · 影响所有用户"
          draft={globalSoulQuery.data?.content || ''}
          filename="global_soul.md"
          missing={globalMissing}
          updatedAt={globalSoulQuery.data?.updated_at}
        />
      </div>
    </ModuleFrame>
  )
}

function SoulEditor({
  icon,
  title,
  description,
  draft,
  onChange,
  editable = false,
  filename,
  missing,
  updatedAt,
  saving = false,
  changed = false,
  onSave,
}: {
  icon: React.ReactNode
  title: string
  description: string
  draft: string
  onChange?: (value: string) => void
  editable?: boolean
  filename: string
  missing: boolean
  updatedAt?: number
  saving?: boolean
  changed?: boolean
  onSave?: () => void
}) {
  const [mode, setMode] = useState<'edit' | 'preview'>(editable ? 'edit' : 'preview')
  const invalid = !draft.trim() || draft.length > MAX_SOUL_CHARS
  const download = () => downloadMarkdown(filename, draft)
  return <article className={`panel ${styles.editorCard}`}>
    <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">{icon}</span><span><strong>{title}</strong><span>{description}</span></span></div><StatusChip status={missing ? 'missing' : changed ? 'warning' : 'saved'}>{missing ? '尚未创建' : changed ? '有未保存修改' : '已同步'}</StatusChip></div>
    <div className={styles.editorToolbar} aria-label={`${title}操作`}>
      {editable && <button type="button" className={mode === 'edit' ? styles.activeTool : ''} aria-label={`编辑${title}`} onClick={() => setMode('edit')}><Pencil size={14} />编辑</button>}
      <button type="button" className={mode === 'preview' ? styles.activeTool : ''} aria-label={`预览${title}`} onClick={() => setMode('preview')}><Eye size={14} />预览</button>
      <button type="button" aria-label={`下载${title}`} onClick={download}><Download size={14} />下载</button>
    </div>
    {editable && mode === 'edit'
      ? <textarea className={styles.soulSurface} aria-label={`${title} Markdown`} value={draft} maxLength={MAX_SOUL_CHARS} spellCheck={false} placeholder={`输入${title} Markdown…`} onChange={(event) => onChange?.(event.target.value)} />
      : <div className={`${styles.soulSurface} ${styles.markdownPreview}`} aria-label={`${title} Markdown 预览`}><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft || `*${title}暂无内容*`}</ReactMarkdown></div>}
    <div className={styles.editorFoot}>
      <span><FilePenLine size={13} />{draft.length.toLocaleString()} / {MAX_SOUL_CHARS.toLocaleString()} 字符 · {updatedAt ? `更新于 ${formatDateTime(updatedAt)}` : '尚无文件'}</span>
      {editable && <button type="button" className={styles.primaryButton} disabled={!changed || invalid || saving} onClick={onSave}><Save size={14} />{saving ? '正在保存…' : missing ? '创建人格文件' : '保存用户人格'}</button>}
    </div>
  </article>
}

function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const objectUrl = typeof URL.createObjectURL === 'function' ? URL.createObjectURL(blob) : `data:text/markdown;charset=utf-8,${encodeURIComponent(content)}`
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  if (objectUrl.startsWith('blob:')) URL.revokeObjectURL(objectUrl)
}
