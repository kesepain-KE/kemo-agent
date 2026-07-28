import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type RefObject,
} from 'react'
import { ChevronUp } from 'lucide-react'

export interface UserMessageMarker {
  id: string
  content: string
  round: number
}

interface UserMessageNavigatorProps {
  markers: UserMessageMarker[]
  scrollContainerRef: RefObject<HTMLElement | null>
  hasEarlierMessages?: boolean
  loadingEarlierMessages?: boolean
  onLoadEarlierMessages?: () => void
  onNavigate: (id: string) => void
}

type WheelCardStyle = CSSProperties & {
  '--wheel-x': string
  '--wheel-y': string
  '--wheel-scale': number
}

const MAX_WHEEL_ITEMS = 20
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

function wheelPosition(index: number, count: number) {
  const angle = count === 1 ? 180 : 90 + (index / Math.max(1, count - 1)) * 180
  const radians = (angle * Math.PI) / 180
  return {
    angle,
    x: 80 + 38 * Math.cos(radians),
    y: 50 - 34 * Math.sin(radians),
  }
}

function wheelCardStyle(index: number, count: number, activeIndex: number): WheelCardStyle {
  const position = wheelPosition(index, count)
  const distance = Math.abs(activeIndex - index)
  return {
    '--wheel-x': `${index === activeIndex ? 70 : position.x + 7}%`,
    '--wheel-y': `${position.y}%`,
    '--wheel-scale': index === activeIndex ? 1 : Math.max(.78, .94 - distance * .025),
    opacity: index === activeIndex ? 1 : Math.max(.2, .72 - distance * .07),
    zIndex: index === activeIndex ? 100 : Math.max(1, 50 - distance),
  }
}

export function UserMessageNavigator({
  markers,
  scrollContainerRef,
  hasEarlierMessages = false,
  loadingEarlierMessages = false,
  onLoadEarlierMessages,
  onNavigate,
}: UserMessageNavigatorProps) {
  const markerIds = markers.map((marker) => marker.id).join('\u0001')
  const visibleMarkers = useMemo(() => markers.slice(-MAX_WHEEL_ITEMS), [markerIds])
  const [viewportActiveId, setViewportActiveId] = useState(markers.at(-1)?.id ?? '')
  const [previewId, setPreviewId] = useState('')
  const [open, setOpen] = useState(false)
  const navigatorRef = useRef<HTMLElement | null>(null)
  const frameRef = useRef<number | null>(null)
  const closeTimerRef = useRef<number | null>(null)
  const lastWheelTimeRef = useRef(0)
  const markersRef = useRef(markers)
  markersRef.current = markers

  const activeId = visibleMarkers.some((marker) => marker.id === previewId)
    ? previewId
    : visibleMarkers.some((marker) => marker.id === viewportActiveId)
      ? viewportActiveId
      : visibleMarkers.at(-1)?.id ?? ''
  const activeIndex = Math.max(0, visibleMarkers.findIndex((marker) => marker.id === activeId))

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
    const marker = visibleMarkers[clamp(index, 0, Math.max(0, visibleMarkers.length - 1))]
    if (!marker) return
    clearCloseTimer()
    setPreviewId(marker.id)
    setOpen(true)
  }, [clearCloseTimer, visibleMarkers])

  const movePreview = useCallback((direction: -1 | 1) => {
    previewAt(activeIndex + direction)
  }, [activeIndex, previewAt])

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
    } else if (event.key === 'Escape') {
      event.preventDefault()
      clearCloseTimer()
      setOpen(false)
      setPreviewId('')
    }
  }, [clearCloseTimer, movePreview])

  useEffect(() => {
    const container = scrollContainerRef.current
    if (!container || markersRef.current.length === 0) return

    const updateActiveMarker = () => {
      const viewportCenter = container.getBoundingClientRect().top + container.clientHeight / 2
      let closestId = markersRef.current[0]?.id ?? ''
      let closestDistance = Number.POSITIVE_INFINITY
      for (const element of markerElements(container)) {
        const id = element.dataset.userMessageId || ''
        if (!markersRef.current.some((marker) => marker.id === id)) continue
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
    return () => {
      container.removeEventListener('scroll', scheduleUpdate)
      if (frameRef.current !== null) {
        if (window.cancelAnimationFrame) window.cancelAnimationFrame(frameRef.current)
        else window.clearTimeout(frameRef.current)
      }
      frameRef.current = null
    }
  }, [markerIds, scrollContainerRef])

  useEffect(() => {
    if (!markers.some((marker) => marker.id === viewportActiveId)) {
      setViewportActiveId(markers.at(-1)?.id ?? '')
    }
    if (!visibleMarkers.some((marker) => marker.id === previewId)) setPreviewId('')
  }, [markerIds, previewId, viewportActiveId, visibleMarkers])

  useEffect(() => () => clearCloseTimer(), [clearCloseTimer])

  if (markers.length < 2 && !hasEarlierMessages) return null

  return (
    <nav
      ref={navigatorRef}
      className="user-message-navigator"
      aria-label="用户消息导航"
      data-open={open}
      onPointerEnter={openWheel}
      onPointerLeave={scheduleClose}
    >
      <div className="user-message-wheel" aria-hidden={!open} aria-label="对话轮次卡片">
        <span className="user-message-wheel-glow" aria-hidden="true" />
        {open ? (
          <>
            {visibleMarkers.map((marker, index) => {
              const preview = compactUserMessagePreview(marker.content)
              const active = index === activeIndex
              const angle = wheelPosition(index, visibleMarkers.length).angle
              return (
                <button
                  key={marker.id}
                  className={`user-message-wheel-card${active ? ' active' : ''}`}
                  style={wheelCardStyle(index, visibleMarkers.length, activeIndex)}
                  type="button"
                  data-wheel-angle={Math.round(angle)}
                  aria-current={active ? 'true' : undefined}
                  aria-label={`第 ${marker.round} 轮：${preview}`}
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
                  {active ? <small>第 {marker.round} 轮</small> : null}
                  <strong>{active ? `“${preview}”` : preview}</strong>
                </button>
              )
            })}
          </>
        ) : null}
      </div>

      <div className="user-message-rail" aria-label="最近 20 条用户消息刻度">
        {hasEarlierMessages && onLoadEarlierMessages ? (
          <button
            className="user-message-navigator-more"
            type="button"
            disabled={loadingEarlierMessages}
            onClick={onLoadEarlierMessages}
            aria-label={loadingEarlierMessages ? '正在加载更早消息' : '加载更早消息'}
            title={loadingEarlierMessages ? '正在加载更早消息…' : '还有更早消息，点击加载'}
          >
            <ChevronUp size={12} />
          </button>
        ) : null}
        <div className="user-message-marker-layer" role="listbox" aria-label="已加载的用户消息，可通过滚轮切换">
          {visibleMarkers.map((marker, index) => {
            const preview = compactUserMessagePreview(marker.content)
            const active = index === activeIndex
            return (
              <button
                key={marker.id}
                className={`user-message-marker${active ? ' active' : ''}`}
                data-navigator-message-id={marker.id}
                type="button"
                role="option"
                aria-selected={active}
                aria-label={`跳转到第 ${marker.round} 轮：${preview}`}
                onPointerEnter={() => previewAt(index)}
                onFocus={() => previewAt(index)}
                onClick={() => activate(marker)}
                onKeyDown={handleKeyDown}
              >
                <span className="user-message-marker-line" aria-hidden="true" />
              </button>
            )
          })}
        </div>
      </div>
    </nav>
  )
}
