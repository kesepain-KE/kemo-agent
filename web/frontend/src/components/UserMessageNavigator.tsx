import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type RefObject,
} from 'react'
import styles from './UserMessageNavigator.module.css'

export interface UserMessageMarker {
  id: string
  content: string
  integration_number: number
}

interface UserMessageNavigatorProps {
  markers: UserMessageMarker[]
  scrollContainerRef: RefObject<HTMLElement | null>
  integrationCount?: number
  navigating?: boolean
  onNavigate: (id: string) => void
}

type WheelCardStyle = CSSProperties & {
  '--wheel-x': string
  '--wheel-y': string
  '--wheel-scale': number
}

type RoundProgressStyle = CSSProperties & {
  '--round-progress': string
}

const CLOSE_DELAY_MS = 180
const WHEEL_THROTTLE_MS = 75

export function compactUserMessagePreview(content: string, limit = 96) {
  const compact = content
    .replace(/```(?:[^\n]*)\n?([\s\S]*?)```/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, ' ')
    .replace(/(^|\s)[#>*_~`-]+(?=\s)/g, '$1')
    .replace(/[*_~`]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!compact) return '空消息'
  return compact.length > limit ? `${compact.slice(0, limit).trimEnd()}…` : compact
}

function markerElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-user-message-id]'))
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), maximum)
}

function wheelAngle(index: number, count: number, activeIndex: number) {
  if (count <= 1 || index === activeIndex) return 180
  if (index < activeIndex) {
    return 90 + (index / Math.max(1, activeIndex)) * 90
  }
  return 180 + ((index - activeIndex) / Math.max(1, count - activeIndex - 1)) * 90
}

function wheelPosition(index: number, count: number, activeIndex: number) {
  const angle = wheelAngle(index, count, activeIndex)
  const radians = (angle * Math.PI) / 180
  return {
    angle,
    // 使用百分比弧线，让预览卡片随聊天区域缩放，不再依赖旧版固定 540px 画布。
    x: 78 + 32 * Math.cos(radians),
    y: 50 - 37 * Math.sin(radians),
  }
}

function wheelCardStyle(index: number, count: number, activeIndex: number): WheelCardStyle {
  const position = wheelPosition(index, count, activeIndex)
  const distance = Math.abs(activeIndex - index)
  return {
    '--wheel-x': `${position.x}%`,
    '--wheel-y': `${position.y}%`,
    '--wheel-scale': index === activeIndex ? 1 : Math.max(.78, .94 - distance * .025),
    opacity: index === activeIndex ? 1 : Math.max(.2, .72 - distance * .07),
    zIndex: index === activeIndex ? 100 : Math.max(1, 50 - distance),
  }
}

export function UserMessageNavigator({
  markers,
  scrollContainerRef,
  integrationCount = 0,
  navigating = false,
  onNavigate,
}: UserMessageNavigatorProps) {
  const markerIds = markers.map((marker) => marker.id).join('\u0001')
  const [viewportActiveId, setViewportActiveId] = useState(markers.at(-1)?.id ?? '')
  const [previewId, setPreviewId] = useState('')
  const [open, setOpen] = useState(false)
  const navigatorRef = useRef<HTMLElement | null>(null)
  const railRef = useRef<HTMLDivElement | null>(null)
  const wheelId = useId()
  const frameRef = useRef<number | null>(null)
  const closeTimerRef = useRef<number | null>(null)
  const lastWheelTimeRef = useRef(Number.NEGATIVE_INFINITY)
  const markersRef = useRef(markers)
  markersRef.current = markers

  const activeId = markers.some((marker) => marker.id === previewId)
    ? previewId
    : markers.some((marker) => marker.id === viewportActiveId)
      ? viewportActiveId
      : markers.at(-1)?.id ?? ''
  const activeIndex = Math.max(0, markers.findIndex((marker) => marker.id === activeId))
  const selectedRound = markers[activeIndex]?.integration_number ?? markers.at(-1)?.integration_number ?? 1
  const globalRoundCount = markers.reduce(
    (count, marker) => Math.max(count, marker.integration_number),
    Math.max(integrationCount, selectedRound),
  )
  const roundProgress = globalRoundCount <= 1
    ? 1
    : clamp((selectedRound - 1) / (globalRoundCount - 1), 0, 1)
  const progressStyle: RoundProgressStyle = {
    '--round-progress': `${roundProgress * 100}%`,
  }

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current === null) return
    window.clearTimeout(closeTimerRef.current)
    closeTimerRef.current = null
  }, [])

  const openWheel = useCallback(() => {
    clearCloseTimer()
    setOpen(true)
  }, [clearCloseTimer])

  const scheduleClose = useCallback(() => {
    clearCloseTimer()
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false)
      setPreviewId('')
    }, CLOSE_DELAY_MS)
  }, [clearCloseTimer])

  const previewAt = useCallback((index: number) => {
    const marker = markers[clamp(index, 0, Math.max(0, markers.length - 1))]
    if (!marker) return
    clearCloseTimer()
    setPreviewId(marker.id)
    setOpen(true)
  }, [clearCloseTimer, markers])

  const movePreview = useCallback((direction: -1 | 1) => {
    const currentMarkers = markersRef.current
    const currentIndex = currentMarkers.findIndex((marker) => marker.id === activeId)
    const marker = currentMarkers[clamp(currentIndex + direction, 0, Math.max(0, currentMarkers.length - 1))]
    if (!marker) return
    clearCloseTimer()
    setPreviewId(marker.id)
    setOpen(true)
  }, [activeId, clearCloseTimer])

  const activate = useCallback((marker: UserMessageMarker) => {
    setViewportActiveId(marker.id)
    setPreviewId(marker.id)
    onNavigate(marker.id)
  }, [onNavigate])

  const handleWheel = useCallback((event: WheelEvent) => {
    event.preventDefault()
    const now = performance.now()
    if (now - lastWheelTimeRef.current < WHEEL_THROTTLE_MS) return
    lastWheelTimeRef.current = now
    if (event.deltaY > 0) movePreview(1)
    else if (event.deltaY < 0) movePreview(-1)
  }, [movePreview])

  useEffect(() => {
    const navigator = navigatorRef.current
    if (!navigator) return
    navigator.addEventListener('wheel', handleWheel, { passive: false })
    return () => navigator.removeEventListener('wheel', handleWheel)
  }, [handleWheel])

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      event.preventDefault()
      movePreview(-1)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      event.preventDefault()
      movePreview(1)
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const marker = event.key === 'Home' ? markersRef.current[0] : markersRef.current.at(-1)
      if (marker) {
        clearCloseTimer()
        setPreviewId(marker.id)
        setOpen(true)
      }
    } else if (event.key === 'Enter' || event.key === ' ') {
      // 卡片使用自身的原生按钮激活；轨道键盘操作才从预选轮次定位。
      if (event.target !== railRef.current) return
      event.preventDefault()
      const marker = markersRef.current.find((item) => item.id === activeId)
      if (open && marker) activate(marker)
      else openWheel()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      clearCloseTimer()
      railRef.current?.focus()
      setOpen(false)
      setPreviewId('')
    }
  }, [activate, activeId, clearCloseTimer, movePreview, open, openWheel])

  useEffect(() => {
    if (!open) return
    const closeOnOutside = (event: PointerEvent) => {
      if (navigatorRef.current?.contains(event.target as Node)) return
      clearCloseTimer()
      setOpen(false)
      setPreviewId('')
    }
    document.addEventListener('pointerdown', closeOnOutside)
    return () => document.removeEventListener('pointerdown', closeOnOutside)
  }, [clearCloseTimer, open])

  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container || markersRef.current.length === 0) return
    const knownIds = new Set(markersRef.current.map((marker) => marker.id))

    const updateActiveMarker = () => {
      const viewportCenter = container.getBoundingClientRect().top + container.clientHeight / 2
      let closestId = markersRef.current[0]?.id ?? ''
      let closestDistance = Number.POSITIVE_INFINITY
      for (const element of markerElements(container)) {
        const id = element.dataset.userMessageId || ''
        if (!knownIds.has(id)) continue
        const rect = element.getBoundingClientRect()
        const distance = Math.abs(rect.top + rect.height / 2 - viewportCenter)
        if (distance < closestDistance) {
          closestDistance = distance
          closestId = id
        }
      }
      setViewportActiveId((current) => current === closestId ? current : closestId)
    }
    const scheduleUpdate = () => {
      if (frameRef.current !== null) return
      const requestFrame = window.requestAnimationFrame ?? ((callback: FrameRequestCallback) => window.setTimeout(() => callback(Date.now()), 16))
      frameRef.current = requestFrame(() => {
        frameRef.current = null
        updateActiveMarker()
      })
    }

    scheduleUpdate()
    container.addEventListener('scroll', scheduleUpdate, { passive: true })
    window.addEventListener('resize', scheduleUpdate)
    return () => {
      container.removeEventListener('scroll', scheduleUpdate)
      window.removeEventListener('resize', scheduleUpdate)
      if (frameRef.current !== null) {
        if (window.cancelAnimationFrame) window.cancelAnimationFrame(frameRef.current)
        else window.clearTimeout(frameRef.current)
      }
      frameRef.current = null
    }
  }, [markerIds, scrollContainerRef])

  useEffect(() => {
    const currentMarkers = markersRef.current
    if (!currentMarkers.some((marker) => marker.id === viewportActiveId)) {
      setViewportActiveId(currentMarkers.at(-1)?.id ?? '')
    }
    if (!currentMarkers.some((marker) => marker.id === previewId)) setPreviewId('')
  }, [markerIds, previewId, viewportActiveId])

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer])

  if (markers.length === 0) return null

  return (
    <nav
      ref={navigatorRef}
      className={`user-message-navigator ${styles.navigator}`}
      aria-label="用户消息导航"
      data-open={open}
      onPointerEnter={(event) => { if (event.pointerType !== 'touch') openWheel() }}
      onPointerLeave={(event) => { if (event.pointerType !== 'touch') scheduleClose() }}
      onFocus={openWheel}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) scheduleClose()
      }}
    >
      <div id={wheelId} className={`user-message-wheel ${styles.wheel}`} aria-hidden={!open} aria-label="对话轮次卡片" role="group">
        <span className={styles.glow} aria-hidden="true" />
        {open ? (
          <>
            {markers.map((marker, index) => {
              const preview = compactUserMessagePreview(marker.content)
              const active = index === activeIndex
              const angle = wheelPosition(index, markers.length, activeIndex).angle
              return (
                <button
                  key={marker.id}
                  className={`user-message-wheel-card ${styles.card}${active ? ` active ${styles.active}` : ''}`}
                  style={wheelCardStyle(index, markers.length, activeIndex)}
                  type="button"
                  data-wheel-angle={Math.round(angle)}
                  aria-current={active ? 'true' : undefined}
                  aria-label={`第 ${marker.integration_number} 轮：${preview}`}
                  onPointerEnter={() => previewAt(index)}
                  onFocus={() => previewAt(index)}
                  onPointerDown={(event) => {
                    if (typeof event.button !== 'number' || event.button === 0) activate(marker)
                  }}
                  onClick={(event) => {
                    if (event.detail === 0) activate(marker)
                  }}
                  onKeyDown={handleKeyDown}
                >
                  {active ? <span className={styles.cardMeta}><small>第 {marker.integration_number} 轮</small><em>{globalRoundCount} 轮对话</em></span> : null}
                  <strong>{active ? `“${preview}”` : preview}</strong>
                  {active ? <span className={styles.cardHint}>点击定位这一轮 <span aria-hidden="true">↗</span></span> : null}
                </button>
              )
            })}
          </>
        ) : null}
      </div>

      <div
        ref={railRef}
        className={`user-message-rail ${styles.rail}`}
        role="button"
        aria-label={`轮次位置：当前预选第 ${selectedRound} 轮，共 ${globalRoundCount} 轮`}
        aria-expanded={open}
        aria-controls={wheelId}
        title="滚轮选轮 · 点击卡片定位"
        onClick={openWheel}
        onFocus={openWheel}
        onKeyDown={handleKeyDown}
        tabIndex={0}
      >
        <span
          className={`user-message-progress ${styles.progress}${navigating ? ` loading ${styles.loading}` : ''}`}
          role="progressbar"
          aria-label="当前预选轮次"
          aria-valuemin={1}
          aria-valuemax={globalRoundCount}
          aria-valuenow={selectedRound}
          style={progressStyle}
        >
          <span className={`user-message-progress-thumb ${styles.thumb}`} />
        </span>
        <span className={styles.railLabel} aria-hidden="true">{selectedRound}</span>
      </div>
      <span className={`user-message-progress-status ${styles.status}`} role="status" aria-live="polite">
        {navigating ? '正在定位消息。' : ''}当前预选第 {selectedRound} 轮，共 {globalRoundCount} 轮
      </span>
    </nav>
  )
}
