import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Image, RefreshCw, Trash2, Upload } from 'lucide-react'
import { backgroundImageMaxBytes, backgroundVideoMaxBytes, backgroundUrl, defaultBackground, deleteBackground, uploadBackground } from '../api/background'
import type { BackgroundSettings, BackgroundStatus } from '../api/background'
import { patchUserConfig } from '../api/client'
import { GlobalConfirmDialog } from '../components/GlobalConfirmDialog'
import { formatBytes } from '../components/ModuleUi'
import { useBackground, useBackgroundPlayback } from '../hooks/useBackground'
import { t, useLanguage } from '../i18n'

export function BackgroundSetting({ user }: { user: string }) {
  useLanguage()
  const query = useBackground(user)
  const playback = useBackgroundPlayback(user)
  const data = query.data?.user === user ? query.data : undefined
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState<BackgroundSettings>({ ...defaultBackground, ...data?.settings })
  const [confirm, setConfirm] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [previewError, setPreviewError] = useState('')
  useEffect(() => { if (data) setDraft({ ...defaultBackground, ...data.settings }) }, [data])
  useEffect(() => {
    client.setQueryData(['background-ui-preview', user], { ui_opacity: draft.ui_opacity, ui_blur: draft.ui_blur })
  }, [client, user, draft.ui_opacity, draft.ui_blur])
  useEffect(() => () => { client.setQueryData(['background-ui-preview', user], null) }, [client, user])
  const save = useMutation({
    mutationFn: async (settings: BackgroundSettings) => {
      await patchUserConfig(user, { web_ui: { background: settings } })
      return settings
    },
    onSuccess: settings => {
      client.setQueryData<BackgroundStatus>(['background', user], previous => previous ? { ...previous, settings } : previous)
      setFeedback('背景设置已保存')
    },
    onError: () => { setDraft({ ...defaultBackground, ...data?.settings }); setFeedback('背景设置保存失败，已恢复原设置') },
  })
  const upload = useMutation({
    mutationFn: (file: File) => uploadBackground(user, file),
    onSuccess: result => {
      client.setQueryData(['background', user], result)
      setPreviewError('')
      setFeedback('背景已上传，可开启背景')
    },
    onError: () => setFeedback('背景上传失败，原背景保持不变'),
  })
  const clear = useMutation({
    mutationFn: () => deleteBackground(user),
    onSuccess: result => {
      client.setQueryData(['background', user], result)
      setConfirm(false)
      setFeedback('背景已清除')
    },
  })
  const pending = save.isPending || upload.isPending || clear.isPending
  const disabled = pending || !data || query.isFetching
  const source = data?.available ? backgroundUrl(user, data.revision) : ''
  const failed = previewError === source && Boolean(source)
    || playback.data?.revision === data?.revision && playback.data?.failed
  const changed = JSON.stringify(draft) !== JSON.stringify(data?.settings ?? defaultBackground)

  return <article className="setting-section background-setting">
    <div className="setting-section-head"><strong>{t('网页背景')}</strong>
      <span>{t('图片或静音视频，仅用于当前用户的网页外观。')}</span></div>
    <div className="background-setting-body">
      <div className="background-controls">
        <div className="background-enable-row">
          <label htmlFor="background-enabled"><strong>{t('启用背景')}</strong>
            <small>{t('关闭只停用显示，不删除背景文件。')}</small></label>
          <button id="background-enabled" className={`switch ${draft.enabled ? 'is-on' : ''}`}
            type="button" role="switch" aria-label={t('启用背景')} aria-checked={draft.enabled}
            disabled={disabled || (!data?.available && !draft.enabled)}
            onClick={() => { const next = { ...draft, enabled: !draft.enabled }; setDraft(next); save.mutate(next) }} />
        </div>
        <label className="background-range" htmlFor="background-overlay">
          <span>{t('遮罩强度')}<output>{Math.round(draft.overlay_opacity * 100)}%</output></span>
          <input id="background-overlay" type="range" min="0" max="100" step="1" disabled={disabled}
            value={Math.round(draft.overlay_opacity * 100)}
            onChange={event => setDraft({ ...draft, overlay_opacity: Number(event.target.value) / 100 })} />
        </label>
        <label className="background-range" htmlFor="background-blur">
          <span>{t('背景模糊')}<output>{draft.blur} px</output></span>
          <input id="background-blur" type="range" min="0" max="20" step="1" disabled={disabled} value={draft.blur}
            onChange={event => setDraft({ ...draft, blur: Number(event.target.value) })} />
        </label>
        <div className="background-ui-controls">
          <label className="background-range" htmlFor="background-ui-transparency">
            <span>{t('UI 透明度')}<output>{Math.round((1 - draft.ui_opacity) * 100)}%</output></span>
            <input id="background-ui-transparency" type="range" min="0" max="100" step="1" disabled={disabled}
              value={Math.round((1 - draft.ui_opacity) * 100)}
              onChange={event => setDraft({ ...draft, ui_opacity: (100 - Number(event.target.value)) / 100 })} />
          </label>
          <label className="background-range" htmlFor="background-ui-blur">
            <span>{t('UI 毛玻璃')}<output>{draft.ui_blur} px</output></span>
            <input id="background-ui-blur" type="range" min="0" max="30" step="1" disabled={disabled} value={draft.ui_blur}
              onChange={event => setDraft({ ...draft, ui_blur: Number(event.target.value) })} />
          </label>
          <p className="background-hint">{t('UI 透明度越高，面板底色越透明；毛玻璃只模糊面板后方，不模糊文字。')}</p>
          <p className="background-hint">{t('启用背景时生效；拖动即时预览，点击应用后保存。透明度 0% 为不透明。')}</p>
        </div>
        <div className="background-actions">
          <button className="btn btn-primary" disabled={disabled || !changed}
            onClick={() => save.mutate(draft)}>{t('应用背景设置')}</button>
          <button className="btn" disabled={disabled} onClick={() => input.current?.click()}>
            <Upload size={14} />{t(data?.available ? '更换背景' : '上传背景')}</button>
          <button className="btn" disabled={disabled || (!data?.available && !data?.error)}
            onClick={() => setConfirm(true)}><Trash2 size={14} />{t('清除背景')}</button>
          <button className="btn" disabled={pending || query.isFetching}
            onClick={() => { setPreviewError(''); void query.refetch() }}><RefreshCw size={14} />{t('重新读取')}</button>
        </div>
        <input ref={input} type="file" className="sr-only" aria-label={t('上传背景')}
          accept=".jpg,.jpeg,.png,.webp,.mp4,.webm" disabled={disabled}
          onChange={event => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (!file) return
            const image = file.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(file.name)
            if (file.size > backgroundVideoMaxBytes || (image && file.size > backgroundImageMaxBytes)) {
              setFeedback('文件过大：图片上限 100 MB，视频上限 400 MB')
              return
            }
            setFeedback('')
            upload.mutate(file)
          }} />
        <p className="background-hint">{t('图片：JPG、PNG、WebP，最大 100 MB；视频：MP4、WebM，最大 400 MB。')}</p>
        <p className="background-hint">{t('视频静音循环，不跟随系统动态效果偏好；播放失败回退原主题。')}</p>
      </div>
      <div className="background-preview-column">
        <div className="background-preview" aria-label={t('背景预览')}>
          {source && !failed ? <>
            {data?.media_kind === 'image'
              ? <img src={source} alt={t('背景预览')} style={{ filter: `blur(${draft.blur}px)` }} onError={() => setPreviewError(source)} />
              : <video src={source} controls muted playsInline preload="metadata" style={{ filter: `blur(${draft.blur}px)` }}
                onError={() => setPreviewError(source)} />}
            <span className="background-preview-shade" style={{ opacity: draft.overlay_opacity }} />
            <span className="background-preview-label">{t(data?.media_kind === 'video' ? '动态背景' : '静态背景')}</span>
          </> : <div className="background-empty"><Image size={28} /><span>{t('未设置可用背景')}</span></div>}
        </div>
        {data?.available && <small className="background-file"><span>{data.filename}</span><span>{formatBytes(data.size)}</span></small>}
      </div>
    </div>
    {query.isError && <div className="settings-inline-feedback" role="alert">{t('背景读取失败，请重试')}</div>}
    {(failed || data?.error) && <div className="settings-inline-feedback" role="alert">{t('背景无法加载或播放，已回退原主题')}</div>}
    {(feedback || pending) && <div className="settings-inline-feedback" role={save.isError || upload.isError ? 'alert' : 'status'}>
      {t(pending ? '保存中…' : feedback)}</div>}
    <GlobalConfirmDialog open={confirm} title={t('清除背景')} icon={<Trash2 size={20} />}
      description={t('确定删除当前用户的背景文件？此操作不会修改其他用户或主题设置。')}
      confirmLabel={t('确认清除')} cancelLabel={t('取消')} pendingLabel={t('清除中…')} pending={clear.isPending}
      error={clear.isError ? t('背景清除失败，请重试') : undefined}
      onConfirm={() => clear.mutate()} onCancel={() => setConfirm(false)} tone="danger" />
  </article>
}
