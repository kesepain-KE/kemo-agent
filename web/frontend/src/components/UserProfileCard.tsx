import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, LogOut, Settings, UserRound, Users, ExternalLink } from 'lucide-react'
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
  onOpenUserSwitch?: () => void
  onOpenSettings?: () => void
  onLogout?: () => void
  newUserTabHref?: string
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
  onOpenUserSwitch,
  onOpenSettings,
  onLogout,
  newUserTabHref,
}: UserProfileCardProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const [position, setPosition] = useState({
    left: 12, top: undefined as number | undefined,
    bottom: 12 as number | undefined, width: 285, maxHeight: 400,
  })
  const switchableUsers = users.filter((item) => item.username !== username)

  useEffect(() => {
    if (!isOpen) return
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!containerRef.current?.contains(target) && !menuRef.current?.contains(target)) setIsOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setIsOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown, true)
    }
  }, [isOpen])

  useEffect(() => { setIsOpen(false) }, [username, compact])

  useLayoutEffect(() => {
    if (!isOpen) return
    // 挂到 body 避开侧栏 overflow:hidden；定位始终使用用户卡片的实际坐标。
    const updatePosition = () => {
      const trigger = triggerRef.current
      if (!trigger) return
      const rect = trigger.getBoundingClientRect()
      if (trigger.closest('[inert], [aria-hidden="true"]')) {
        setIsOpen(false)
        return
      }
      const margin = 12
      const gap = 10
      const width = Math.min(285, window.innerWidth - margin * 2)
      const roomAbove = rect.top - gap - margin
      const roomBelow = window.innerHeight - rect.bottom - gap - margin
      const toSide = compact && window.innerWidth - rect.right - gap - margin >= width
      const upward = roomAbove >= roomBelow
      setPosition({
        width,
        left: Math.max(margin, Math.min(toSide ? rect.right + gap : rect.left, window.innerWidth - width - margin)),
        top: !toSide && !upward ? rect.bottom + gap : undefined,
        bottom: toSide ? Math.max(margin, window.innerHeight - rect.bottom)
          : upward ? window.innerHeight - rect.top + gap : undefined,
        maxHeight: toSide ? Math.max(0, Math.min(window.innerHeight - margin * 2, rect.bottom - margin))
          : Math.max(0, upward ? roomAbove : roomBelow),
      })
    }
    updatePosition()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updatePosition)
    if (triggerRef.current) observer?.observe(triggerRef.current)
    const sidebar = triggerRef.current?.closest('.sidebar')
    if (sidebar) observer?.observe(sidebar)
    // 移动导航隐藏时关闭 portal，避免父级 inert 失去约束后留下孤立菜单。
    const visibilityObserver = sidebar ? new MutationObserver(updatePosition) : undefined
    if (sidebar) visibilityObserver?.observe(sidebar, { attributes: true, attributeFilter: ['inert', 'aria-hidden'] })
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      observer?.disconnect()
      visibilityObserver?.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [isOpen, compact])

  const runAndClose = (action?: () => void) => {
    action?.()
    setIsOpen(false)
  }

  return (
    <div className={`${styles.wrapper} ${compact ? styles.compactWrapper : ''}`} ref={containerRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`${styles.profileCard} ${compact ? styles.compactCard : ''} ${isOpen ? styles.profileCardOpen : ''}`}
        aria-label="切换当前用户"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-controls={isOpen ? menuId : undefined}
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

      {isOpen && createPortal(<div ref={menuRef} id={menuId} className={styles.menu}
        style={position} role="menu" aria-label="用户菜单"
        onKeyDown={(event) => {
          if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const options = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])
          if (!options.length) return
          const index = options.indexOf(document.activeElement as HTMLElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
          options[next]?.focus()
        }}>
        <div className={styles.menuIdentity}>
          <span className={styles.menuAvatar}><AvatarContent username={username} url={avatarUrl} /></span>
          <span><strong>{username}</strong><small>{userPath}</small></span>
          <Check size={15} aria-hidden="true" />
        </div>
        <button type="button" className={styles.menuItem} role="menuitem" onClick={() => runAndClose(onOpenProfile)}><UserRound size={16} /><span>用户资料</span></button>
        {newUserTabHref && <a className={styles.menuItem} role="menuitem" href={newUserTabHref} target="_blank" rel="noopener noreferrer" onClick={() => setIsOpen(false)}><ExternalLink size={16} /><span>新建此用户标签页</span></a>}
        <button type="button" className={styles.menuItem} role="menuitem" onClick={() => runAndClose(onOpenUserSwitch)}><Users size={16} /><span>切换用户</span></button>
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
      </div>, document.body)}
    </div>
  )
}
