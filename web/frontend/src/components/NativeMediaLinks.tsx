import { File, FileImage, FileMusic, FileVideo, PanelRight } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { t, useLanguage } from '../i18n'
import { openWorkspacePath } from '../store/workspace'
import { nativeMediaStatus, toolNativeInputs } from './nativeMedia'
import styles from './NativeMediaLinks.module.css'

export function NativeMediaLinks({ item, user }: { item: Extract<ChatItem, { kind: 'tool' }>; user: string }) {
  useLanguage()
  const inputs = toolNativeInputs(item)
  if (!inputs.length || !user) return null
  return <div className={styles.files} aria-label={t('工具装载的媒体')}>
    {inputs.map(input => {
      const Icon = ({ image: FileImage, audio: FileMusic, video: FileVideo, file: File })[input.media_kind]
      return <div className={styles.file} key={input.asset_id}>
        <Icon size={15} aria-hidden="true" />
        <span className={styles.copy}><strong title={input.name}>{input.name}</strong>
          <small title={input.status === 'included' ? t('携带媒体的请求已获得完整响应，不代表模型已正确理解。') : input.error}>
            {nativeMediaStatus(input.status)}{!input.available ? ` · ${t('原文件不可预览')}` : ''}
          </small></span>
        {input.error && <span className={styles.error} role="status">{input.error}</span>}
        {input.available && input.scope !== 'external' && <button type="button" className="btn btn-ghost btn-sm"
          aria-label={`${t('在工作面板打开')} ${input.name}`} onClick={() => openWorkspacePath(user, input.scope as 'download' | 'file_upload', input.relative_path, input.name, input.size)}>
          <PanelRight size={14} aria-hidden="true" />{t('预览')}
        </button>}
      </div>
    })}
    <p className={styles.hint}>{t('仅在本轮请求中装载；预览不会触发模型请求。')}</p>
  </div>
}
