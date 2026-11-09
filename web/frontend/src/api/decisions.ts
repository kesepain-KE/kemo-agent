import { z } from 'zod'
import { requestJson } from './transport'
import { decisionsSchema, userDecisionSchema } from './contracts'
export type UserDecision = z.infer<typeof userDecisionSchema>

const path = (user: string, sessionId: string) => (
  `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/decisions`
)
export function getDecisions(user: string, sessionId: string, signal?: AbortSignal) {
  return requestJson<z.infer<typeof decisionsSchema>>(`${path(user, sessionId)}?source=web`, { signal })
}
export function answerDecision(user: string, sessionId: string, decisionId: string, answer: { option_id: string; text: string }) {
  return requestJson<{ decision: UserDecision }>(
    `${path(user, sessionId)}/${encodeURIComponent(decisionId)}/answer?source=web`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(answer) },
  )
}
