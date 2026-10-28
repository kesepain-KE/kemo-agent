import { StrictMode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BackgroundSetting } from './BackgroundSetting'
import { AppBackground } from '../components/AppBackground'
import type { BackgroundStatus } from '../api/background'
import { defaultBackground } from '../api/background'
import { server } from '../test/server'
import { useUiStore } from '../store/ui'

const status = (user = 'alice'): BackgroundStatus => ({
  user, available: true, filename: 'background.webp', media_kind: 'image', mime_type: 'image/webp',
  size: 1024, revision: 'v1', error: '', settings: { ...defaultBackground },
})
beforeEach(() => useUiStore.getState().setLanguage('zh-CN', 'alice'))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function mount(initial = status()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(['background', initial.user], initial)
  client.setQueryData(['user-config', initial.user], { config: { provider: { model: 'unsaved-draft' } } })
  const view = render(<QueryClientProvider client={client}><BackgroundSetting user={initial.user} /></QueryClientProvider>)
  return { client, ...view }
}

describe('background settings', () => {
  it('places both UI sliders in the background card without a system-motion dependency', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    mount({ ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    for (const label of [/UI 透明度/, /UI 毛玻璃/]) {
      expect(screen.getByRole('slider', { name: label }).closest('.background-setting')).not.toBeNull()
    }
    expect(screen.queryByText('系统已启用减少动态效果，视频背景以静态画面显示。')).not.toBeInTheDocument()
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows metadata and saves only background settings without invalidating provider drafts', async () => {
    let body: unknown
    server.use(http.patch('/api/users/alice/config', async ({ request }) => {
      body = await request.json()
      return HttpResponse.json({ updated: true })
    }))
    const { client } = mount()
    expect(screen.getByText('background.webp')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '背景预览' })).toHaveAttribute('src', '/api/users/alice/background?v=v1')
    fireEvent.change(screen.getByRole('slider', { name: /遮罩强度/ }), { target: { value: '42' } })
    fireEvent.change(screen.getByRole('slider', { name: /背景模糊/ }), { target: { value: '7' } })
    fireEvent.change(screen.getByRole('slider', { name: /UI 透明度/ }), { target: { value: '35' } })
    fireEvent.change(screen.getByRole('slider', { name: /UI 毛玻璃/ }), { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: '应用背景设置' }))
    expect(await screen.findByText('背景设置已保存')).toBeInTheDocument()
    expect(body).toEqual({ changes: { web_ui: { background: { enabled: false, overlay_opacity: 0.42, blur: 7, ui_opacity: 0.65, ui_blur: 12 } } } })
    expect(client.getQueryData(['background', 'alice'])).toMatchObject({ settings: { overlay_opacity: 0.42, blur: 7 } })
    expect(client.getQueryData(['user-config', 'alice'])).toEqual({ config: { provider: { model: 'unsaved-draft' } } })
  })

  it('enables immediately and restores settings after a failed save', async () => {
    server.use(http.patch('/api/users/alice/config', () => HttpResponse.json({ error: { message: 'failed' } }, { status: 500 })))
    mount()
    fireEvent.click(screen.getByRole('switch', { name: '启用背景' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('背景设置保存失败，已恢复原设置')
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  })

  it('uploads via multipart into the current user resource and refreshes preview', async () => {
    const append = vi.spyOn(FormData.prototype, 'append')
    server.use(http.post('/api/users/alice/background', () => {
      return HttpResponse.json({ ...status(), filename: 'background.png', revision: 'v2' })
    }))
    mount()
    fireEvent.change(screen.getByLabelText('上传背景', { selector: 'input' }), {
      target: { files: [new File(['bytes'], 'my-picture.png', { type: 'image/png' })] },
    })
    expect(await screen.findByText('背景已上传，可开启背景')).toBeInTheDocument()
    expect(append).toHaveBeenCalledWith('file', expect.objectContaining({ name: 'my-picture.png' }))
    expect(screen.getByRole('img', { name: '背景预览' })).toHaveAttribute('src', '/api/users/alice/background?v=v2')
  })

  it('preserves the existing preview after an upload failure', async () => {
    server.use(http.post('/api/users/alice/background', () => HttpResponse.json({ error: { message: 'bad file' } }, { status: 400 })))
    mount()
    fireEvent.change(screen.getByLabelText('上传背景', { selector: 'input' }), { target: { files: [new File(['bad'], 'bad.png')] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('背景上传失败')
    expect(screen.getByText('background.webp')).toBeInTheDocument()
  })

  it.each([['large.webp', 'image/webp', 101], ['large.png', '', 101], ['large.mp4', 'video/mp4', 401]])(
    'blocks oversized %s uploads before a network request', async (name, type, size) => {
    let called = false
    server.use(http.post('/api/users/alice/background', () => { called = true; return HttpResponse.json(status()) }))
    mount()
    const file = new File(['small'], name, { type })
    Object.defineProperty(file, 'size', { value: size * 1024 * 1024 })
    fireEvent.change(screen.getByLabelText('上传背景', { selector: 'input' }), { target: { files: [file] } })
    expect(await screen.findByText('文件过大：图片上限 100 MB，视频上限 400 MB')).toBeInTheDocument()
    expect(called).toBe(false)
  })

  it.each([['exact.webp', 'image/webp', 100], ['exact.mp4', 'video/mp4', 400]])(
    'accepts %s at the five-times larger limit', async (name, type, size) => {
      let called = false
      server.use(http.post('/api/users/alice/background', () => { called = true; return HttpResponse.json(status()) }))
      mount()
      const file = new File(['small'], name, { type })
      Object.defineProperty(file, 'size', { value: size * 1024 * 1024 })
      fireEvent.change(screen.getByLabelText('上传背景', { selector: 'input' }), { target: { files: [file] } })
      expect(await screen.findByText('背景已上传，可开启背景')).toBeInTheDocument()
      expect(called).toBe(true)
    })

  it('distinguishes disabling from deletion and requires confirmation to clear', async () => {
    let calls = 0
    server.use(http.delete('/api/users/alice/background', () => {
      calls++
      return HttpResponse.json({ ...status(), available: false, filename: '', revision: '', deleted: true })
    }))
    mount()
    fireEvent.click(screen.getByRole('button', { name: '清除背景' }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(calls).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '清除背景' }))
    fireEvent.click(screen.getByRole('button', { name: '确认清除' }))
    expect(await screen.findByText('背景已清除')).toBeInTheDocument()
    expect(calls).toBe(1)
    expect(screen.getByRole('switch')).toBeDisabled()
  })

  it('keeps the confirmation open on clear failure and permits retry', async () => {
    server.use(http.delete('/api/users/alice/background', () => HttpResponse.json({ error: { message: 'disk locked' } }, { status: 500 })))
    mount()
    fireEvent.click(screen.getByRole('button', { name: '清除背景' }))
    fireEvent.click(screen.getByRole('button', { name: '确认清除' }))
    expect(await screen.findByText('背景清除失败，请重试')).toBeInTheDocument()
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(screen.getByText('background.webp')).toBeInTheDocument()
  })

  it('shows preview failure instead of a broken image', async () => {
    mount()
    fireEvent.error(screen.getByRole('img', { name: '背景预览' }))
    expect(screen.getByRole('alert')).toHaveTextContent('背景无法加载或播放，已回退原主题')
    expect(screen.queryByRole('img', { name: '背景预览' })).not.toBeInTheDocument()
  })

  it('late completion cannot change a newly selected user', async () => {
    let finish!: () => void
    const gate = new Promise<void>(resolve => { finish = resolve })
    server.use(http.patch('/api/users/alice/config', async () => { await gate; return HttpResponse.json({ updated: true }) }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['background', 'alice'], status())
    client.setQueryData(['background', 'bob'], status('bob'))
    const tree = (user: string) => <QueryClientProvider client={client}><BackgroundSetting key={user} user={user} /></QueryClientProvider>
    const view = render(tree('alice'))
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(client.isMutating()).toBe(1))
    view.rerender(tree('bob'))
    await act(async () => { finish(); await gate })
    await waitFor(() => expect(client.isMutating()).toBe(0))
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('img')).toHaveAttribute('src', '/api/users/bob/background?v=v1')
    expect(client.getQueryData(['background', 'alice'])).toMatchObject({ settings: { enabled: true } })
    expect(client.getQueryData(['background', 'bob'])).toMatchObject({ settings: { enabled: false } })
  })
})

describe('shell background lifecycle', () => {
  function shell(initial: BackgroundStatus) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['background', initial.user], initial)
    const tree = (user: string) => <QueryClientProvider client={client}><div className="app-shell">
      <AppBackground key={user} user={user} /><main className="app-main">conversation draft</main>
    </div></QueryClientProvider>
    return { client, tree, ...render(tree(initial.user)) }
  }

  it('activates only after load and removes the old user background before the next render', () => {
    const initial = status()
    initial.settings.enabled = true
    const view = shell(initial)
    const root = view.container.querySelector('.app-shell')
    expect(root).not.toHaveAttribute('data-background')
    fireEvent.load(view.container.querySelector('.web-background img')!)
    expect(root).toHaveAttribute('data-background', 'on')
    view.rerender(view.tree('bob'))
    expect(root).not.toHaveAttribute('data-background')
    expect(view.container.querySelector('.web-background img')).toBeNull()
    expect(screen.getByText('conversation draft')).toBeInTheDocument()
  })

  it('removes the active styling if an image fails', () => {
    const initial = status()
    initial.settings.enabled = true
    const view = shell(initial)
    const image = view.container.querySelector('.web-background img')!
    fireEvent.load(image)
    fireEvent.error(image)
    expect(view.container.querySelector('.app-shell')).not.toHaveAttribute('data-background')
    expect(view.client.getQueryData(['background-playback', 'alice'])).toMatchObject({ failed: true, revision: 'v1' })
  })

  it('keeps one video across page rerenders, pauses when hidden, and releases it on user switch', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const initial = { ...status(), media_kind: 'video' as const, filename: 'background.mp4', settings: { ...defaultBackground, enabled: true } }
    const view = shell(initial)
    const element = view.container.querySelector('video')!
    await waitFor(() => expect(view.container.querySelector('.app-shell')).toHaveAttribute('data-background', 'on'))
    view.rerender(view.tree('alice'))
    expect(view.container.querySelector('video')).toBe(element)
    expect(play).toHaveBeenCalledTimes(1)
    expect(element.muted).toBe(true)
    expect(element.loop).toBe(true)
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    expect(pause).toHaveBeenCalled()
    hidden.mockReturnValue(false)
    fireEvent(document, new Event('visibilitychange'))
    await waitFor(() => expect(play).toHaveBeenCalledTimes(2))
    view.rerender(view.tree('bob'))
    expect(element).not.toHaveAttribute('src')
    expect(load).toHaveBeenCalled()
    expect(view.container.querySelector('video')).toBeNull()
  })

  it('falls back if automatic playback is rejected', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('blocked'))
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const view = shell({ ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    await waitFor(() => expect(view.client.getQueryData(['background-playback', 'alice'])).toMatchObject({ failed: true }))
    expect(view.container.querySelector('.app-shell')).not.toHaveAttribute('data-background')
  })

  it('plays video even when system animations are disabled', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const view = shell({ ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    const video = view.container.querySelector('video')!
    expect(video).toHaveAttribute('preload', 'auto')
    expect(view.container.querySelector('.app-shell')).not.toHaveAttribute('data-background')
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.querySelector('.app-shell')).toHaveAttribute('data-background', 'on'))
    expect(view.container.querySelector('.app-shell')).toHaveAttribute('data-background', 'on')
    expect(video.muted && video.loop).toBe(true)
    expect(view.client.getQueryData(['background-playback', 'alice'])).toMatchObject({ revision: 'v1', failed: false })
  })

  it('does not subscribe to OS motion preferences or restart playback on preference changes', async () => {
    const media = { matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }
    vi.stubGlobal('matchMedia', () => media)
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const view = shell({ ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    const video = view.container.querySelector('video')!
    Object.defineProperty(video, 'readyState', { configurable: true, value: 2 })
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1))
    act(() => { media.matches = true; view.rerender(view.tree('alice')) })
    expect(media.addEventListener).not.toHaveBeenCalled()
    expect(pause).not.toHaveBeenCalled()
    expect(video).toHaveAttribute('src', '/api/users/alice/background?v=v1')
    expect(view.container.querySelector('.app-shell')).toHaveAttribute('data-background', 'on')
    expect(load).not.toHaveBeenCalled()
    act(() => { media.matches = false; view.rerender(view.tree('alice')) })
    expect(play).toHaveBeenCalledTimes(1)
    expect(view.container.querySelector('video')).toBe(video)
    expect(load).not.toHaveBeenCalled()
  })

  it('still falls back and reports genuine decode errors, not system preferences', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const view = shell({ ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    fireEvent.error(view.container.querySelector('video')!)
    expect(view.container.querySelector('.app-shell')).not.toHaveAttribute('data-background')
    expect(view.client.getQueryData(['background-playback', 'alice'])).toMatchObject({ failed: true })
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('applies per-user saved UI variables without fading the whole shell', () => {
    const view = shell({ ...status(), settings: { ...defaultBackground, ui_opacity: 0.6, ui_blur: 12 } })
    const root = view.container.querySelector<HTMLElement>('.app-shell')!
    expect(root.style.getPropertyValue('--background-ui-opacity')).toBe('60%')
    expect(root.style.getPropertyValue('--background-ui-blur')).toBe('12px')
    expect(root.style.opacity).toBe('')
    view.rerender(view.tree('bob'))
    expect(root.style.getPropertyValue('--background-ui-opacity')).toBe('94%')
    expect(root.style.getPropertyValue('--background-ui-blur')).toBe('0px')
  })

  it('previews unsaved UI settings and restores persisted appearance when leaving settings', async () => {
    const initial = { ...status(), settings: { ...defaultBackground, enabled: true } }
    const view = shell(initial)
    const panel = render(<QueryClientProvider client={view.client}><BackgroundSetting user="alice" /></QueryClientProvider>)
    fireEvent.change(screen.getByRole('slider', { name: /UI 透明度/ }), { target: { value: '40' } })
    fireEvent.change(screen.getByRole('slider', { name: /UI 毛玻璃/ }), { target: { value: '20' } })
    const root = view.container.querySelector<HTMLElement>('.app-shell')!
    await waitFor(() => expect(root.style.getPropertyValue('--background-ui-opacity')).toBe('60%'))
    expect(root.style.getPropertyValue('--background-ui-blur')).toBe('20px')
    expect(view.client.getQueryData(['background', 'alice'])).toMatchObject({ settings: { ...defaultBackground, enabled: true } })
    panel.unmount()
    await waitFor(() => expect(root.style.getPropertyValue('--background-ui-opacity')).toBe('94%'))
    expect(root.style.getPropertyValue('--background-ui-blur')).toBe('0px')
  })

  it('restores the source during StrictMode effect replay and releases it on unmount', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const client = new QueryClient()
    client.setQueryData(['background', 'alice'], { ...status(), media_kind: 'video', settings: { ...defaultBackground, enabled: true } })
    const view = render(<StrictMode><QueryClientProvider client={client}><AppBackground user="alice" /></QueryClientProvider></StrictMode>)
    const video = view.container.querySelector('video')!
    await waitFor(() => expect(video).toHaveAttribute('src', '/api/users/alice/background?v=v1'))
    view.unmount()
    expect(video).not.toHaveAttribute('src')
  })
})
