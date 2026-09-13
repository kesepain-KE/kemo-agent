import type { PendingNextTurnMessage } from './appShellTypes'

export function reorderFollowUps(queue: PendingNextTurnMessage[], messageId: string, targetId: string) {
  const from = queue.findIndex((item) => item.id === messageId)
  const to = queue.findIndex((item) => item.id === targetId)
  if (from < 0 || to < 0 || from === to) return queue
  if (queue.slice(Math.min(from, to), Math.max(from, to) + 1).some((item) => item.status === 'sending' || item.status === 'guiding')) return queue
  const next = [...queue]
  const [message] = next.splice(from, 1)
  next.splice(to, 0, message)
  return next
}
