import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from './test/server'
import { chatSessionUrl, forgetSession, readRememberedSession, rememberSession, restoreWebSession } from './sessionNavigation'

afterEach(() => {
  forgetSession('kesepain')
  forgetSession('reviewer')
})

describe('Web 会话恢复', () => {
  it('刷新原标签页保留自己的空间，新标签页恢复该用户最近的空间', () => {
    rememberSession('kesepain', 'first')
    localStorage.setItem('kemo-last-web-session:kesepain', 'other-tab')
    expect(readRememberedSession('kesepain')).toBe('first')
    sessionStorage.removeItem('kemo-last-web-session:kesepain')
    expect(readRememberedSession('kesepain')).toBe('other-tab')
    expect(readRememberedSession('reviewer')).toBe('')
  })

  it('优先验证最近访问的空间，不因后台更新其他会话而恢复到其他空间', async () => {
    rememberSession('kesepain', 'selected')
    let restores = 0
    server.use(http.get('/api/users/kesepain/sessions/active', () => {
      restores += 1
      return HttpResponse.json({ session: { session_id: 'other' } })
    }))
    const result = await restoreWebSession('kesepain', 'web_restore_test')
    expect(result.session.session_id).toBe('selected')
    expect(restores).toBe(0)
  })

  it('有记忆 ID 时只选原目标，不抢在 AppShell 的核验前回退或续租', async () => {
    rememberSession('kesepain', 'closed')
    const touches = vi.fn(() => HttpResponse.json({ leased: false }, { status: 404 }))
    const restores = vi.fn(() => HttpResponse.json({ session: { session_id: 'other' } }))
    server.use(
      http.post('/api/users/kesepain/sessions/closed/lease', touches),
      http.get('/api/users/kesepain/sessions/active', restores),
    )
    const result = await restoreWebSession('kesepain', 'web_restore_test')
    expect(result.session.session_id).toBe('closed')
    expect(readRememberedSession('kesepain')).toBe('closed')
    expect(touches).not.toHaveBeenCalled()
    expect(restores).not.toHaveBeenCalled()
  })

  it('没有记忆 ID 才查询后端 active，断网错误交给恢复页', async () => {
    const restores = vi.fn(() => HttpResponse.error())
    server.use(http.get('/api/users/kesepain/sessions/active', restores))
    await expect(restoreWebSession('kesepain', 'web_restore_test')).rejects.toThrow()
    expect(restores).toHaveBeenCalledOnce()
    expect(readRememberedSession('kesepain')).toBe('')
  })

  it('旧会话删除通知不会清除其他标签页刚选中的空间', () => {
    rememberSession('kesepain', 'selected')
    forgetSession('kesepain', 'older')
    expect(readRememberedSession('kesepain')).toBe('selected')
    forgetSession('kesepain', 'selected')
    expect(readRememberedSession('kesepain')).toBe('')
    expect(chatSessionUrl('a b', 'selected')).toBe('/chat?user=a+b&session=selected')
  })

  it('后台心跳只保存本标签页空间，不抢占其他页面最近访问的空间', () => {
    rememberSession('kesepain', 'foreground')
    rememberSession('kesepain', 'background', false)
    expect(readRememberedSession('kesepain')).toBe('background')
    sessionStorage.removeItem('kemo-last-web-session:kesepain')
    expect(readRememberedSession('kesepain')).toBe('foreground')
  })
})
