import { afterEach, describe, expect, it, vi } from 'vitest'
import { playUserCompletionSound } from './completionSound'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('playUserCompletionSound', () => {
  it('在 Windows 桌面端播放用户专属音效', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const play = vi.fn().mockResolvedValue(undefined)
    const AudioMock = vi.fn(function AudioMock(this: { play: typeof play }, _url: string) {
      this.play = play
    })
    vi.stubGlobal('Audio', AudioMock)

    await expect(playUserCompletionSound('alice')).resolves.toBe(true)
    expect(AudioMock).toHaveBeenCalledOnce()
    expect(String(AudioMock.mock.calls[0][0])).toContain('/api/users/alice/completion-sound')
    expect(String(AudioMock.mock.calls[0][0])).not.toContain('?v=')
    expect(play).toHaveBeenCalledOnce()
  })

  it('在手机端不创建 Audio，也不请求后端降级播放', async () => {
    const AudioMock = vi.fn()
    const fetchMock = vi.fn()
    vi.stubGlobal('Audio', AudioMock)
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 15; Mobile)')
    await expect(playUserCompletionSound('alice')).resolves.toBe(false)
    expect(AudioMock).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('浏览器拒绝播放时请求 Windows 后端降级音效', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const rejectedAudio = vi.fn(function RejectedAudio(this: { play: () => Promise<never> }) {
      this.play = () => Promise.reject(new Error('autoplay blocked'))
    })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      user: 'alice', played: true, mode: 'system_notification', reason: 'browser_fallback',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('Audio', rejectedAudio)
    vi.stubGlobal('fetch', fetchMock)

    await expect(playUserCompletionSound('alice')).resolves.toBe(true)
    expect(rejectedAudio).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/users/alice/completion-sound/fallback')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' })
  })

  it('浏览器和后端降级都失败时静默返回 false', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    vi.stubGlobal('Audio', vi.fn(function RejectedAudio(this: { play: () => Promise<never> }) {
      this.play = () => Promise.reject(new Error('autoplay blocked'))
    }))
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    await expect(playUserCompletionSound('alice')).resolves.toBe(false)
  })
})
