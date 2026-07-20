import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, FilePenLine, Globe2, RefreshCw, Save, ShieldAlert, UserRound } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import {
  ApiError,
  getGlobalSoul,
  getUserAvatarUrl,
  getUserSoul,
  updateGlobalSoul,
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
  const [globalDraft, setGlobalDraft] = useState('')
  const [selectedAvatar, setSelectedAvatar] = useState<File | null>(null)
  const [avatarNotice, setAvatarNotice] = useState('')
  const [avatarFailed, setAvatarFailed] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(() => Date.now())
  const [confirmGlobal, setConfirmGlobal] = useState(false)
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
    setGlobalDraft(globalSoulQuery.data?.content || '')
  }, [globalSoulQuery.data?.content])
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
  const globalSoulMutation = useMutation({
    mutationFn: updateGlobalSoul,
    onSuccess: (result) => {
      queryClient.setQueryData(['global-soul'], result)
      setConfirmGlobal(false)
      setSaveNotice('全局人格已原子写入，并将影响所有用户。')
    },
    onError: (error) => setSaveNotice(error instanceof Error ? error.message : '全局人格保存失败'),
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
  const globalChanged = globalDraft !== (globalSoulQuery.data?.content || '')

  return (
    <ModuleFrame
      kicker="Identity & Soul"
      title="用户资料"
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
          draft={globalDraft}
          onChange={setGlobalDraft}
          missing={globalMissing}
          updatedAt={globalSoulQuery.data?.updated_at}
          saving={globalSoulMutation.isPending}
          changed={globalChanged}
          dangerous
          confirm={confirmGlobal}
          onRequestSave={() => setConfirmGlobal(true)}
          onCancel={() => setConfirmGlobal(false)}
          onSave={() => globalSoulMutation.mutate(globalDraft)}
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
  missing,
  updatedAt,
  saving,
  changed,
  dangerous = false,
  confirm = false,
  onRequestSave,
  onCancel,
  onSave,
}: {
  icon: React.ReactNode
  title: string
  description: string
  draft: string
  onChange: (value: string) => void
  missing: boolean
  updatedAt?: number
  saving: boolean
  changed: boolean
  dangerous?: boolean
  confirm?: boolean
  onRequestSave?: () => void
  onCancel?: () => void
  onSave: () => void
}) {
  const invalid = !draft.trim() || draft.length > MAX_SOUL_CHARS
  return <article className={`panel ${styles.editorCard}`}>
    <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">{icon}</span><span><strong>{title}</strong><span>{description}</span></span></div><StatusChip status={missing ? 'missing' : changed ? 'warning' : 'saved'}>{missing ? '尚未创建' : changed ? '有未保存修改' : '已同步'}</StatusChip></div>
    {dangerous && <div className={styles.warning}><ShieldAlert size={15} /><span><strong>全局安全边界</strong><small>保存后立即成为所有用户的全局人格来源，请确认内容不削弱系统安全规则。</small></span></div>}
    <textarea aria-label={`${title} Markdown`} value={draft} maxLength={MAX_SOUL_CHARS} spellCheck={false} placeholder={`输入${title} Markdown…`} onChange={(event) => onChange(event.target.value)} />
    <div className={styles.editorFoot}>
      <span><FilePenLine size={13} />{draft.length.toLocaleString()} / {MAX_SOUL_CHARS.toLocaleString()} 字符 · {updatedAt ? `更新于 ${formatDateTime(updatedAt)}` : '尚无文件'}</span>
      {!dangerous || !confirm ? <button type="button" className={dangerous ? styles.dangerButton : styles.primaryButton} disabled={!changed || invalid || saving} onClick={dangerous ? onRequestSave : onSave}><Save size={14} />{saving ? '正在保存…' : dangerous ? '审核并保存' : missing ? '创建人格文件' : '保存用户人格'}</button> : <span className={styles.confirmActions}><button type="button" onClick={onCancel}>取消</button><button type="button" className={styles.dangerButton} disabled={invalid || saving} onClick={onSave}>{saving ? '正在保存…' : '确认覆盖全局人格'}</button></span>}
    </div>
  </article>
}
