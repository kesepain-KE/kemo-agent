import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSessionChannel, getPageClientId, nextLeaseSequence, type SessionChannelEvent } from './sessionClient'

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
  it('orders heartbeats, releases and remounts within one document', () => {
    const id = getPageClientId()
    const heartbeat = nextLeaseSequence(id)
    const release = nextLeaseSequence(id)
    const resumed = nextLeaseSequence(id)
    expect(release).toBe(heartbeat + 1)
    expect(resumed).toBe(release + 1)
    expect(nextLeaseSequence('another-document')).toBe(1)
    expect(getPageClientId()).toBe(id)
  })
  it('new documents do not reuse a cloned tab storage identity', async () => {
    const first = getPageClientId()
    vi.resetModules()
    const nextDocument = await import('./sessionClient')
    expect(nextDocument.getPageClientId()).not.toBe(first)
  })
  it.each(['session-deleted', 'session-updated'] as const)('把 %s 通知发送给同浏览器的其他页面', (type) => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    const received: SessionChannelEvent[] = []
    const first = createSessionChannel(() => undefined)
    const second = createSessionChannel((event) => received.push(event))
    const event: SessionChannelEvent = {
      type,
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
