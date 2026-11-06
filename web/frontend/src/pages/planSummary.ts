import type { PlanSummary } from '../types/api'

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function extractPlanSummary(value: unknown): PlanSummary | null {
  let payload = objectValue(value)
  const wrapped = objectValue(payload?.result)
  if (payload?.ok === true && wrapped) payload = wrapped
  const raw = objectValue(payload?.plan) || (payload?.plan_id ? payload : null)
  if (!raw || typeof raw.plan_id !== 'string' || typeof raw.title !== 'string' || !Array.isArray(raw.steps)) return null
  const steps = raw.steps.map((value) => objectValue(value)).filter((step): step is Record<string, unknown> => Boolean(step)).map((step) => ({
    step_id: String(step.step_id || ''), title: String(step.title || ''), description: String(step.description || ''), status: String(step.status || 'pending'),
    depends_on: Array.isArray(step.depends_on) ? step.depends_on.map(String) : [], critical: Boolean(step.critical ?? true), tool_name: String(step.tool_name || ''), started_at: String(step.started_at || ''), finished_at: String(step.finished_at || ''),
  }))
  const completed = steps.filter((step) => step.status === 'completed' || step.status === 'skipped').length
  return { plan_id: raw.plan_id, title: raw.title, description: String(raw.description || ''), status: String(raw.status || 'pending'), auto_accept: Boolean(raw.auto_accept), reminder: String(raw.reminder || ''), source: String(raw.source || ''), session_id: String(raw.session_id || ''), current_step: String(raw.current_step || ''), revision: Number(raw.revision || 1), created_at: String(raw.created_at || ''), updated_at: String(raw.updated_at || ''), progress: { completed, total: steps.length, percent: steps.length ? Math.round(completed * 100 / steps.length) : 0 }, steps }
}
