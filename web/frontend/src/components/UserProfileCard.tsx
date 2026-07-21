import { useEffect, useRef, useState } from 'react'
import { Check, LogOut, Settings, UserRound } from 'lucide-react'
import styles from './UserProfileCard.module.css'

export interface UserProfileOption {
  username: string
  userPath?: string
  avatarUrl?: string
}

export interface UserProfileCardProps {
  username?: string
  userPath?: string
  avatarUrl?: string
  users?: UserProfileOption[]
  compact?: boolean
  logoutPending?: boolean
  switchingDisabled?: boolean
  switchingDisabledReason?: string
  onSelectUser?: (username: string) => void
  onOpenProfile?: () => void
  onOpenSettings?: () => void
  onLogout?: () => void
}

function initialFor(username: string) {
  return username.trim().charAt(0).toUpperCase() || 'K'
}

function AvatarContent({ username, url, imageClassName }: { username: string; url?: string; imageClassName?: string }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [url])
  return url && !failed
    ? <img className={imageClassName} src={url} alt="" onError={() => setFailed(true)} />
    : <>{initialFor(username)}</>
}

export function UserProfileCard({
  username = 'kesepain',
  userPath = 'users/kesepain',
  avatarUrl,
  users = [],
  compact = false,
  logoutPending = false,
  switchingDisabled = false,
  switchingDisabledReason = '当前暂不可切换用户',
  onSelectUser,
  onOpenProfile,
  onOpenSettings,
  onLogout,
}: UserProfileCardProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const switchableUsers = users.filter((item) => item.username !== username)

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setIsOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const runAndClose = (action?: () => void) => {
    action?.()
    setIsOpen(false)
  }

  return (
    <div className={`${styles.wrapper} ${compact ? styles.compactWrapper : ''}`} ref={containerRef}>
      <button
        type="button"
        className={`${styles.profileCard} ${compact ? styles.compactCard : ''} ${isOpen ? styles.profileCardOpen : ''}`}
        aria-label="切换当前用户"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        title={compact ? username : undefined}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span className={styles.avatar}>
          <span className={styles.avatarInitial}><AvatarContent username={username} url={avatarUrl} imageClassName={styles.avatarImage} /></span>
        </span>
        <span className={styles.userInfo}>
          <span className={styles.username}>{username}</span>
          <span className={styles.userPath}>{userPath}</span>
        </span>
        <span className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`} aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6.5 9L12 14.5L17.5 9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </span>
      </button>

      {isOpen && <div className={`${styles.menu} ${compact ? styles.compactMenu : ''}`} role="menu" aria-label="用户菜单">
        <div className={styles.menuIdentity}>
          <span className={styles.menuAvatar}><AvatarContent username={username} url={avatarUrl} /></span>
          <span><strong>{username}</strong><small>{userPath}</small></span>
          <Check size={15} aria-hidden="true" />
        </div>
        <button type="button" className={styles.menuItem} role="menuitem" onClick={() => runAndClose(onOpenProfile)}><UserRound size={16} /><span>用户资料</span></button>
        <button type="button" className={styles.menuItem} role="menuitem" onClick={() => runAndClose(onOpenSettings)}><Settings size={16} /><span>用户设置</span></button>

        {switchableUsers.length > 0 && <>
          <div className={styles.divider} />
          <div className={styles.menuLabel}>切换用户</div>
          {switchingDisabled && <div className={styles.switchingNotice} role="status">{switchingDisabledReason}</div>}
          <div className={styles.userOptions}>
            {switchableUsers.map((item) => <button
              type="button"
              className={styles.userOption}
              role="menuitem"
              key={item.username}
              disabled={switchingDisabled}
              aria-description={switchingDisabled ? switchingDisabledReason : undefined}
              onClick={() => runAndClose(() => onSelectUser?.(item.username))}
            >
              <span className={styles.optionAvatar}><AvatarContent username={item.username} url={item.avatarUrl} /></span>
              <span><strong>{item.username}</strong><small>{item.userPath || `users/${item.username}`}</small></span>
            </button>)}
          </div>
        </>}

        {onLogout && <>
          <div className={styles.divider} />
          <button type="button" className={`${styles.menuItem} ${styles.dangerItem}`} role="menuitem" disabled={logoutPending} onClick={() => runAndClose(onLogout)}><LogOut size={16} /><span>{logoutPending ? '正在退出…' : '退出登录'}</span></button>
        </>}
      </div>}
    </div>
  )
}
