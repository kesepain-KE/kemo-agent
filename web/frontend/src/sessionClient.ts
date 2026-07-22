export type SessionChannelEvent = {
  type: 'session-deleted'
  user: string
  sessionId: string
  clientId: string
}

const pageClientId = `web_${crypto.randomUUID().replaceAll('-', '')}`
const channelName = 'kemo-agent-sessions-v1'

export function getPageClientId() {
  return pageClientId
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
    if (event?.type === 'session-deleted') onEvent(event)
  }
  return {
    post: (event: SessionChannelEvent) => channel.postMessage(event),
    close: () => channel.close(),
  }
}
