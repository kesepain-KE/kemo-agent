import { randomUUID } from './randomId'

export type SessionChannelEvent = {
  type: 'session-deleted' | 'session-updated'
  user: string
  sessionId: string
  clientId: string
}

const pageClientId = `web_${randomUUID().replaceAll('-', '')}`
const channelName = 'kemo-agent-sessions-v1'
const leaseSequences = new Map<string, number>()

export function getPageClientId() {
  return pageClientId
}

/** One document keeps ordering through StrictMode remounts and BFCache resume.
 * Other documents (including duplicated tabs) always have a different ID.
 */
export function nextLeaseSequence(clientId: string) {
  const next = (leaseSequences.get(clientId) || 0) + 1
  if (!Number.isSafeInteger(next)) throw new Error('Page presence sequence exhausted')
  leaseSequences.set(clientId, next)
  return next
}

export function createSessionChannel(
  onEvent: (event: SessionChannelEvent) => void,
) {
  if (typeof BroadcastChannel === 'undefined') {
    return { post: (_event: SessionChannelEvent) => undefined, close: () => undefined }
  }
  const channel = new BroadcastChannel(channelName)
  channel.onmessage = (message: MessageEvent<SessionChannelEvent>) => {
    const event = message.data
    if (event?.type === 'session-deleted' || event?.type === 'session-updated') onEvent(event)
  }
  return {
    post: (event: SessionChannelEvent) => channel.postMessage(event),
    close: () => channel.close(),
  }
}
