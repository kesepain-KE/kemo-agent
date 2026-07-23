import { useEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
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

export function UserMessageNavigator({
  markers,
  scrollContainerRef,
  hasEarlierMessages = false,
  loadingEarlierMessages = false,
  onLoadEarlierMessages,
  onNavigate,
}: UserMessageNavigatorProps) {
  const [activeId, setActiveId] = useState(markers.at(-1)?.id ?? '')
  const [preview, setPreview] = useState<{ marker: UserMessageMarker; top: number; right: number } | null>(null)
  const indexRef = useRef<HTMLDivElement | null>(null)
  const frameRef = useRef<number | null>(null)
  const markersRef = useRef(markers)
  markersRef.current = markers
  const markerIds = markers.map((marker) => marker.id).join('\u0001')

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
      setActiveId((current) => current === closestId ? current : closestId)
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
    const index = indexRef.current
    if (!index) return
    const active = Array.from(index.querySelectorAll<HTMLElement>('[data-navigator-message-id]'))
      .find((element) => element.dataset.navigatorMessageId === activeId)
    if (!active) return
    const activeTop = active.offsetTop
    const activeBottom = activeTop + active.offsetHeight
    if (activeTop < index.scrollTop) index.scrollTop = activeTop
    else if (activeBottom > index.scrollTop + index.clientHeight) index.scrollTop = activeBottom - index.clientHeight
  }, [activeId, markers.length])

  useEffect(() => {
    const index = indexRef.current
    if (!index || markers.length === 0) return
    index.scrollTop = index.scrollHeight
  }, [markers.at(-1)?.id])

  const showPreview = (marker: UserMessageMarker, element: HTMLElement) => {
    const rect = element.getBoundingClientRect()
    setPreview({
      marker,
      top: Math.min(window.innerHeight - 72, Math.max(72, rect.top + rect.height / 2)),
      right: Math.max(12, window.innerWidth - rect.left + 10),
    })
  }

  if (markers.length < 2 && !hasEarlierMessages) return null

  return (
    <nav className="user-message-navigator" aria-label="用户消息导航">
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
      <div className="user-message-marker-layer" ref={indexRef} tabIndex={0} aria-label="已加载的用户消息，可单独滚动" onScroll={() => setPreview(null)}>
        {markers.map((marker) => {
          const preview = compactUserMessagePreview(marker.content)
          const active = marker.id === activeId
          return (
            <button
              key={marker.id}
              className={`user-message-marker${active ? ' active' : ''}`}
              data-navigator-message-id={marker.id}
              type="button"
              aria-current={active ? 'location' : undefined}
              aria-label={`跳转到第 ${marker.round} 轮：${preview}`}
              onMouseEnter={(event) => showPreview(marker, event.currentTarget)}
              onMouseLeave={() => setPreview(null)}
              onFocus={(event) => showPreview(marker, event.currentTarget)}
              onBlur={() => setPreview(null)}
              onClick={() => {
                setActiveId(marker.id)
                onNavigate(marker.id)
              }}
            >
              <span className="user-message-marker-line" aria-hidden="true" />
            </button>
          )
        })}
      </div>
      {preview ? createPortal(<span className="user-message-marker-preview" role="tooltip" style={{ top: preview.top, right: preview.right }}>
        <small>第 {preview.marker.round} 轮</small>
        <strong>“{compactUserMessagePreview(preview.marker.content)}”</strong>
      </span>, document.body) : null}
    </nav>
  )
}
