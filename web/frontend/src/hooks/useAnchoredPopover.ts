import { useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react'

export function useAnchoredPopover(
  open: boolean,
  anchorRef: RefObject<HTMLElement | null>,
  { minWidth, maxHeight, preferAbove = false, align = 'end' }: {
    minWidth: number
    maxHeight: number
    preferAbove?: boolean
    align?: 'start' | 'end'
  },
): CSSProperties {
  const [position, setPosition] = useState<CSSProperties>({
    position: 'fixed', left: 12, top: 12, bottom: 'auto', right: 'auto', width: minWidth, maxHeight,
  })

  useLayoutEffect(() => {
    if (!open) return
    const update = () => {
      const rect = anchorRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(Math.max(rect.width, minWidth), window.innerWidth - 24)
      const anchorTop = Math.max(12, Math.min(rect.top, window.innerHeight - 12))
      const anchorBottom = Math.max(12, Math.min(rect.bottom, window.innerHeight - 12))
      const above = Math.max(0, anchorTop - 20)
      const below = Math.max(0, window.innerHeight - anchorBottom - 20)
      const upward = preferAbove ? above >= 180 || above >= below : below < maxHeight && above > below
      setPosition({
        position: 'fixed',
        left: Math.max(12, Math.min(align === 'start' ? rect.left : rect.right - width, window.innerWidth - width - 12)),
        right: 'auto',
        // Reset the opposite edge explicitly: removing an inline value can restore a CSS offset.
        top: upward ? 'auto' : anchorBottom + 8,
        bottom: upward ? window.innerHeight - anchorTop + 8 : 'auto',
        width,
        maxHeight: Math.min(maxHeight, upward ? above : below),
      })
    }
    update()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(update)
    if (anchorRef.current) {
      observer?.observe(anchorRef.current)
      const container = anchorRef.current.closest('.composer-inner, .reference-model-menu')
      if (container) observer?.observe(container)
    }
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [open, anchorRef, minWidth, maxHeight, preferAbove, align])

  return position
}
