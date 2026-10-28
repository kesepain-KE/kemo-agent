import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { backgroundUrl, defaultBackground } from '../api/background'
import { useBackground, useBackgroundUiPreview } from '../hooks/useBackground'

/** One shell-owned media element: page navigation must never restart playback. */
export function AppBackground({ user }: { user: string }) {
  const query = useBackground(user)
  const client = useQueryClient()
  const preview = useBackgroundUiPreview(user)
  const data = query.data?.user === user ? query.data : undefined
  const layer = useRef<HTMLDivElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const [loaded, setLoaded] = useState('')
  const [failed, setFailed] = useState('')
  const appearance = { ...defaultBackground, ...data?.settings, ...preview.data }
  const source = data?.available && data.settings.enabled
    ? backgroundUrl(user, data.revision) : ''
  const active = Boolean(source && loaded === source && failed !== source)

  useLayoutEffect(() => {
    const shell = layer.current?.closest('.app-shell')
    if (active) shell?.setAttribute('data-background', 'on')
    else shell?.removeAttribute('data-background')
    return () => { shell?.removeAttribute('data-background') }
  }, [active, source])

  useLayoutEffect(() => {
    const shell = layer.current?.closest<HTMLElement>('.app-shell')
    shell?.style.setProperty('--background-ui-opacity', `${appearance.ui_opacity * 100}%`)
    shell?.style.setProperty('--background-ui-blur', `${appearance.ui_blur}px`)
    return () => {
      shell?.style.removeProperty('--background-ui-opacity')
      shell?.style.removeProperty('--background-ui-blur')
    }
  }, [appearance.ui_opacity, appearance.ui_blur])

  const fail = () => {
    setFailed(source)
    client.setQueryData(['background-playback', user], { revision: data?.revision, failed: true })
  }
  const ready = () => {
    setLoaded(source)
    setFailed('')
    client.setQueryData(['background-playback', user], { revision: data?.revision, failed: false })
  }

  useEffect(() => {
    const element = video.current
    if (!element || !source) return
    // Restore the source on the StrictMode effect replay after cleanup.
    element.setAttribute('src', source)
    let disposed = false
    const sync = () => {
      if (document.hidden) element.pause()
      else {
        element.muted = true
        void element.play().then(() => {
          if (!disposed) ready()
        }).catch(() => {
          if (!disposed && !document.hidden) fail()
        })
      }
    }
    document.addEventListener('visibilitychange', sync)
    sync()
    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', sync)
      element.pause()
      element.removeAttribute('src')
      element.load()
    }
    // Event callbacks intentionally belong to this exact user/source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])

  return <div ref={layer} className="web-background" aria-hidden="true" data-ready={active}
    style={{ '--background-blur': `${data?.settings.blur ?? 0}px` } as React.CSSProperties}>
    {source && data?.media_kind === 'image' && <img key={source} src={source} alt="" onLoad={ready} onError={fail} />}
    {source && data?.media_kind === 'video' && <video key={source} ref={video} src={source}
      muted loop playsInline preload="auto" onError={fail} />}
    <div className="web-background-shade" style={{ opacity: data?.settings.overlay_opacity ?? 0.65 }} />
  </div>
}
