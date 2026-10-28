import { z } from 'zod'

const object = z.object({}).passthrough()
const number = z.number().finite()
const counts = z.record(z.string(), number)
const progress = z.object({ completed: number, total: number, percent: number }).passthrough()
const step = z.object({ step_id: z.string(), title: z.string(), status: z.string(),
  depends_on: z.array(z.string()) }).passthrough()
const plan = z.object({ plan_id: z.string(), title: z.string(), status: z.string(),
  revision: number, source: z.string(), session_id: z.string(),
  progress, steps: z.array(step) }).passthrough()
export const tasksSchema = z.object({
  user: z.string(), summary: z.object({ active_plans: number, waiting_plans: number,
    enabled_crons: number, completed_plans: number }).passthrough(),
  plans: z.array(plan), cron_tasks: z.array(object), executions: z.array(object),
}).passthrough()
export const runtimeSchema = z.object({
  user: z.string(),
  api: z.object({ type: z.string(), model: z.string(), configured: z.boolean() }).passthrough(),
  context: z.object({ percent: number, used_tokens: number, max_tokens: number }).passthrough(),
  tokens: z.object({ date: z.string(), timezone: z.string(), sent_tokens: number,
    received_tokens: number, total_tokens: number, cached_tokens: number,
    cache_rate: number, request_count: number, estimated: z.boolean(), trend: z.array(number) }).passthrough(),
  prompt: z.object({ content: z.string(), total_chars: number, estimated_tokens: number,
    components: z.array(z.object({ id: z.string(), name: z.string(), state: z.string(),
      chars: number, tokens: number, source_files: z.array(z.string()),
      injected_items: number, original_items: number }).passthrough()) }).passthrough(),
  message_routes: z.object({ summary: z.object({ connected_transports: number,
    total_transports: number }).passthrough(), routes: z.array(object) }).passthrough(),
  system_cron: z.object({ tracking: z.string(), tasks: z.array(object), executions: z.array(object) }).passthrough(),
  congestion: z.object({
    provider: z.object({ active_requests: number, max_requests: number,
      available_requests: number, waiting_estimate: number }).passthrough(),
    web: z.object({ active_chats: number, max_chats: number,
      pending_chats: number, max_pending: number }).passthrough(),
    message_router: z.object({ active_workers: number, max_workers: number,
      queued_messages: number, max_queued: number }).passthrough(),
  }).passthrough(),
}).passthrough()
export const knowledgeSchema = z.object({
  user: z.string(), summary: z.object({ documents: number, user_documents: number,
    shared_documents: number, global_documents: number }).passthrough(),
  documents: z.array(z.object({ scope: z.string(), relative_path: z.string(),
    title: z.string(), size: number, updated_at: number, active_for_main_agent: z.boolean() }).passthrough()),
}).passthrough()
export const memorySchema = z.object({ user: z.string(), summary: counts, items: z.array(object) }).passthrough()

/** Endpoint contracts validate before a page can dereference response fields.
 * Unknown optional extension fields are preserved, never synthesized as data. */
export function validateApiResponse(path: string, value: unknown): unknown {
  const pathname = path.split('?')[0]
  const schema = pathname.endsWith('/runtime/status') ? runtimeSchema
    : /\/users\/[^/]+\/tasks$/.test(pathname) ? tasksSchema
    : /\/users\/[^/]+\/knowledge$/.test(pathname) ? knowledgeSchema
    : pathname.endsWith('/memory/summary') ? memorySchema
    : undefined
  if (!schema) return value
  const parsed = schema.safeParse(value)
  if (!parsed.success) {
    const fields = parsed.error.issues.map(issue => issue.path.join('.')).join(', ')
    // Do not include response values (may contain private user content).
    throw new Error(`API response contract mismatch (${pathname}): ${fields}`)
  }
  return parsed.data
}
