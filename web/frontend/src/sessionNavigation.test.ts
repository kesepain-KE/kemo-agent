import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'
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

  it('已关闭或删除的缓存空间不隐式重开，回退到后端的合法活跃绑定', async () => {
    rememberSession('kesepain', 'closed')
    server.use(http.post('/api/users/kesepain/sessions/closed/lease', () => (
      HttpResponse.json({ error: { message: 'closed' } }, { status: 404 })
    )))
    const result = await restoreWebSession('kesepain', 'web_restore_test')
    expect(result.session.session_id).toBe('s1')
    expect(readRememberedSession('kesepain')).toBe('')
  })

  it('重启期间断网不清掉最近空间，也不创建替代会话', async () => {
    rememberSession('kesepain', 'selected')
    server.use(http.post('/api/users/kesepain/sessions/selected/lease', () => HttpResponse.error()))
    await expect(restoreWebSession('kesepain', 'web_restore_test')).rejects.toThrow()
    expect(readRememberedSession('kesepain')).toBe('selected')
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
