import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSessionChannel, type SessionChannelEvent } from './sessionClient'

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []
  onmessage: ((event: MessageEvent<SessionChannelEvent>) => void) | null = null

  constructor(public readonly name: string) {
    FakeBroadcastChannel.instances.push(this)
  }

  postMessage(message: SessionChannelEvent) {
    for (const instance of FakeBroadcastChannel.instances) {
      if (instance !== this && instance.name === this.name) {
        instance.onmessage?.({ data: message } as MessageEvent<SessionChannelEvent>)
      }
    }
  }

  close() {
    FakeBroadcastChannel.instances = FakeBroadcastChannel.instances.filter((value) => value !== this)
  }
}

afterEach(() => {
  FakeBroadcastChannel.instances = []
  vi.unstubAllGlobals()
})

describe('session channel', () => {
  it('把会话删除通知发送给同浏览器的其他页面', () => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const received: SessionChannelEvent[] = []
    const first = createSessionChannel(() => undefined)
    const second = createSessionChannel((event) => received.push(event))
    const event: SessionChannelEvent = {
      type: 'session-deleted',
      user: 'kesepain',
      sessionId: 's1',
      clientId: 'web_client_a',
    }

    first.post(event)

    expect(received).toEqual([event])
    first.close()
    second.close()
  })
})
