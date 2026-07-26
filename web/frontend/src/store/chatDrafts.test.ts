import { afterEach, describe, expect, it } from 'vitest'
import { chatDraftKey, useChatDraftStore } from './chatDrafts'

afterEach(() => useChatDraftStore.getState().clearAll())

describe('chat draft store', () => {
  it('按用户和会话隔离草稿', () => {
    const first = chatDraftKey('kesepain', 'session-a')
    const second = chatDraftKey('kesepain', 'session-b')
    const otherUser = chatDraftKey('reviewer', 'session-a')
    const store = useChatDraftStore.getState()

    store.setText(first, '会话 A 草稿')
    store.setPendingUploads(second, [{ path: 'b.txt', name: 'b.txt', size: 1 }])

    expect(useChatDraftStore.getState().drafts[first]?.text).toBe('会话 A 草稿')
    expect(useChatDraftStore.getState().drafts[second]?.pendingUploads).toHaveLength(1)
    expect(useChatDraftStore.getState().drafts[otherUser]).toBeUndefined()
  })

  it('首轮创建真实会话后迁移未发送状态', () => {
    const temporary = chatDraftKey('kesepain', '')
    const committed = chatDraftKey('kesepain', 'web-created')
    const store = useChatDraftStore.getState()
    store.setText(temporary, '运行中追加的草稿')
    store.setPendingUploads(temporary, [{ path: 'later.png', name: 'later.png', size: 12 }])

    store.moveDraft(temporary, committed)

    expect(useChatDraftStore.getState().drafts[temporary]).toBeUndefined()
    expect(useChatDraftStore.getState().drafts[committed]).toEqual(expect.objectContaining({
      text: '运行中追加的草稿',
      pendingUploads: [{ path: 'later.png', name: 'later.png', size: 12 }],
    }))
  })
})
