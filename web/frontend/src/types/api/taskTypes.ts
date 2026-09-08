export interface PlanStepSummary {
  step_id: string
  title: string
  description: string
  status: string
  depends_on: string[]
  critical: boolean
  tool_name: string
  tool_arguments?: Record<string, unknown>
  result?: unknown
  error?: unknown
  started_at: string
  finished_at: string
}

export interface PlanSummary {
  plan_id: string
  title: string
  description: string
  status: string
  auto_accept: boolean
  reminder: string
  source: string
  session_id: string
  current_step: string
  revision: number
  created_at: string
  updated_at: string
  progress: { completed: number; total: number; percent: number }
  steps: PlanStepSummary[]
}

export interface PlanMutationResponse {
  user: string
  plan: PlanSummary
  updated: boolean
  activated: boolean
  reason: 'auto_retry_on_fix' | 'auto_accept' | 'activation_disabled' | 'plan_not_failed' | 'reset_only' | string
  step_id?: string
  action?: string
}

export interface PlanRevisionSummary {
  plan_id: string
  revision: number
  note: string
  created_at: string
}

export interface PlanRevisionSnapshot {
  schema_version: number
  plan_id: string
  title: string
  description: string
  user: string
  source: string
  session_id: string
  status: string
  auto_accept: boolean
  reminder: string
  revision: number
  created_at: string
  updated_at: string
  current_step: string
  steps: Array<{
    step_id: string
    title: string
    description: string
    status: string
    depends_on: string[]
    tool_name?: string | null
    tool_arguments?: Record<string, unknown>
    critical: boolean
    result?: unknown
    error?: unknown
    started_at: string
    finished_at: string
  }>
}

export interface PlanRevisionsResponse {
  user: string
  plan_id: string
  revisions: PlanRevisionSummary[]
  total: number
}

export interface PlanRevisionResponse {
  user: string
  plan_id: string
  revision: number
  plan: PlanRevisionSnapshot
}

export interface PlanRollbackResponse {
  user: string
  plan_id: string
  target_revision: number
  plan: PlanSummary
  updated: boolean
}

export interface CronTaskSummary {
  task_id: string
  title: string
  user_defined: boolean
  status: string
  type: 'daily' | 'once' | 'recurring'
  time?: string
  interval_seconds?: number
  next_run_at: string
  latest_run_at: string
  created_at: string
  last_state: 'failed' | 'completed' | 'never' | string
}

export interface TasksResponse {
  user: string
  summary: {
    active_plans: number
    waiting_plans: number
    enabled_crons: number
    completed_plans: number
  }
  plans: PlanSummary[]
  cron_tasks: CronTaskSummary[]
  executions: Array<{
    kind: 'plan_step' | 'cron' | string
    task_id: string
    title: string
    status: string
    updated_at: string
    result?: unknown
    error?: unknown
  }>
}
