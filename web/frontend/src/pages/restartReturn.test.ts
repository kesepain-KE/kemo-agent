import { afterEach, describe, expect, it, vi } from 'vitest'
import { scheduleRestartReturn } from './restartReturn'
import { forgetSession, rememberSession } from '../sessionNavigation'
import { getHealth } from '../api/client'

afterEach(() => { vi.useRealTimers(); forgetSession('kesepain') })

describe('重启后的对话回跳', () => {
  it('等待后端恢复后返回当前空间，清掉配置页的临时参数', async () => {
    vi.useFakeTimers()
    const health = vi.fn<typeof getHealth>().mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue({ status: 'ok' } as Awaited<ReturnType<typeof getHealth>>)
    const navigate = vi.fn()
    const fail = vi.fn()
    scheduleRestartReturn('kesepain', 's1', fail, health, navigate)
    await vi.advanceTimersByTimeAsync(4_000)
    expect(navigate).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(navigate).toHaveBeenCalledWith('/chat?user=kesepain&session=s1')
    expect(fail).not.toHaveBeenCalled()
  })

  it('配置页暂时无 session 参数时使用缓存的最近空间', async () => {
    vi.useFakeTimers()
    rememberSession('kesepain', 'recent')
    const health = vi.fn<typeof getHealth>().mockResolvedValue({ status: 'ok' } as Awaited<ReturnType<typeof getHealth>>)
    const navigate = vi.fn()
    scheduleRestartReturn('kesepain', '', vi.fn(), health, navigate)
    await vi.advanceTimersByTimeAsync(4_000)
    expect(navigate).toHaveBeenCalledWith('/chat?user=kesepain&session=recent')
  })

  it('用户离开配置页后取消等待，迟到响应不把用户带走', async () => {
    vi.useFakeTimers()
    let resolve!: (value: Awaited<ReturnType<typeof getHealth>>) => void
    const health = vi.fn<typeof getHealth>(() => new Promise(done => { resolve = done }))
    const navigate = vi.fn()
    const cancel = scheduleRestartReturn('kesepain', 's1', vi.fn(), health, navigate)
    await vi.advanceTimersByTimeAsync(4_000)
    cancel()
    resolve({ status: 'ok' } as Awaited<ReturnType<typeof getHealth>>)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('服务持续离线时有界停止，保留会话链接供重试', async () => {
    vi.useFakeTimers()
    rememberSession('kesepain', 'recent')
    const health = vi.fn<typeof getHealth>().mockRejectedValue(new Error('offline'))
    const navigate = vi.fn()
    const fail = vi.fn()
    scheduleRestartReturn('kesepain', 'recent', fail, health, navigate)
    await vi.advanceTimersByTimeAsync(65_000)
    expect(fail).toHaveBeenCalledOnce()
    expect(navigate).not.toHaveBeenCalled()
  })
})
