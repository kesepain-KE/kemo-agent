import { t as tr, useLanguage } from '../i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Download, Eye, FilePenLine, Globe2, Pencil, Save, UserRound } from 'lucide-react'
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
import { formatBytes, formatDateTime, ModuleError, RefreshActionButton } from '../components/ModuleUi'
import { DocumentMarkdown } from '../components/DocumentMarkdown'

const MAX_SOUL_CHARS = 65_536
const MAX_AVATAR_BYTES = 5 * 1024 * 1024
const AVATAR_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])

function isNotFound(error: unknown) {
  return error instanceof ApiError && error.status === 404
}

export function ProfilePage() {
  useLanguage()
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
    queryFn: ({ signal }) => getUserSoul(user, signal),
    enabled: Boolean(user),
    retry: (count, error) => !isNotFound(error) && count < 1,
  })
  const globalSoulQuery = useQuery({
    queryKey: ['global-soul'],
    queryFn: ({ signal }) => getGlobalSoul(signal),
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
  const soulRefreshing = userSoulQuery.isFetching || globalSoulQuery.isFetching

  return (
    <div className="page profile-page"><div className="page-inner">
      <div className="page-head"><div className="page-head-copy"><h1 className="page-title">{tr("身份与人格")}</h1>
        <p className="page-desc">用户人格可编辑，全局人格在界面上只读。下载的是浏览器本地的当前草稿，不是服务端文件。</p></div>
        <div className="page-head-actions"><RefreshActionButton pending={soulRefreshing} label={tr("重新读取")} pendingLabel={tr("读取中…")} onClick={() => { void userSoulQuery.refetch(); void globalSoulQuery.refetch(); setAvatarRevision(Date.now()) }} /></div></div>
      {((userSoulQuery.isError && !userMissing) || (globalSoulQuery.isError && !globalMissing)) && <ModuleError message="人格文件读取失败，请检查文件编码和访问权限。" />}
      {saveNotice && <div className="banner banner-info" role="status">{saveNotice}<button className="icon-btn" onClick={() => setSaveNotice('')}>×</button></div>}

      <article className="card card-pad" style={{ display: 'flex', gap: 'var(--s-5)', alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div className="avatar-frame">
          {!avatarFailed || previewUrl ? <img src={previewUrl || getUserAvatarUrl(user, avatarRevision)} alt={`${user} 的头像`} onError={() => setAvatarFailed(true)} /> : <span>{user.slice(0, 1).toUpperCase() || 'K'}</span>}
        </div>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontSize: 'var(--t-xl)', fontWeight: 620 }}>{user || tr('未选择用户')}</div>
          <div className="faint mono" style={{ fontSize: 'var(--t-xs)', marginTop: 3 }}>users/{user}</div>
          <input hidden ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={(event) => chooseAvatar(event.target.files?.[0])} />
          <div style={{ display: 'flex', gap: 'var(--s-2)', marginTop: 'var(--s-4)', flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-outline btn-sm" onClick={() => fileInputRef.current?.click()}><Camera size={15} />选择头像</button>
            <button type="button" className="btn btn-outline btn-sm" disabled={!selectedAvatar || avatarMutation.isPending} onClick={() => selectedAvatar && avatarMutation.mutate(selectedAvatar)}>{avatarMutation.isPending ? '正在上传…' : '上传并覆盖'}</button>
          </div>
          <p className="faint" style={{ fontSize: 'var(--t-2xs)', marginTop: 'var(--s-3)' }}>{selectedAvatar ? `${selectedAvatar.name} · ${formatBytes(selectedAvatar.size)}` : 'PNG / JPEG / GIF / WebP，最大 5 MB'}</p>
          {avatarNotice && <div role="status">{avatarNotice}</div>}
        </div>
      </article>

      <div className="editor-grid">
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
    </div></div>
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
  useLanguage()
  const [mode, setMode] = useState<'edit' | 'preview'>('preview')
  const invalid = !draft.trim() || draft.length > MAX_SOUL_CHARS
  const download = () => downloadMarkdown(filename, draft)
  return <article className="editor-panel">
    <div className="editor-head"><span>{icon}</span><div style={{ flex: 1, minWidth: 0 }}><div className="editor-title">{title}</div><div className="faint mono" style={{ fontSize: 'var(--t-2xs)', marginTop: 2 }}>{description}</div></div><span className={`badge ${changed ? 'badge-warning' : ''}`}>{missing ? '尚未创建' : changed ? tr('有未保存修改') : '已同步'}</span></div>
    <div className="editor-body">
      {!editable && <div className="banner banner-warning" style={{ marginBottom: 'var(--s-4)' }}>全局人格为所有用户共享的安全底线，界面不提供保存入口。</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)', marginBottom: 'var(--s-3)' }} aria-label={`${title}操作`}>
        <div className="segments"><button type="button" className={`segment ${mode === 'preview' ? 'is-active' : ''}`} aria-label={`预览${title}`} onClick={() => setMode('preview')}><Eye size={14} />{tr("预览")}</button>
          <button type="button" disabled={!editable} className={`segment ${mode === 'edit' ? 'is-active' : ''}`} aria-label={`编辑${title}`} onClick={() => setMode('edit')}><Pencil size={14} />{tr("编辑")}</button></div>
        <div className="spacer" />{!editable && <span className="badge">{tr("只读")}</span>}
      </div>
    {editable && mode === 'edit'
      ? <textarea className="textarea mono" rows={18} aria-label={`${title} Markdown`} value={draft} maxLength={MAX_SOUL_CHARS} spellCheck={false} placeholder={`输入${title} Markdown…`} onChange={(event) => onChange?.(event.target.value)} />
      : <div className="doc-content profile-soul-preview" role="region" tabIndex={0} aria-label={`${title} Markdown 预览`}><DocumentMarkdown content={draft || `*${title}暂无内容*`} /></div>}
    </div><div className="editor-foot profile-editor-foot">
      <span className="profile-editor-meta"><FilePenLine size={13} />{draft.length.toLocaleString()} / {MAX_SOUL_CHARS.toLocaleString()} 字符 · {updatedAt ? `更新于 ${formatDateTime(updatedAt)}` : '尚无文件'}</span>
      <div className="profile-editor-actions">
      <button className="btn btn-ghost btn-sm" type="button" aria-label={`下载${title}`} onClick={download}><Download size={14} />下载草稿</button>
      {editable && <button type="button" className="btn btn-primary btn-sm" disabled={!changed || invalid || saving} onClick={onSave}><Save size={14} />{saving ? '正在保存…' : missing ? '创建人格文件' : '保存用户人格'}</button>}
      </div>
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
