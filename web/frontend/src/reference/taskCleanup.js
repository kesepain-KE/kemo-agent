import { h, Current, Overlay, Api, icon, mutate, toast } from './shared.js'

const LABELS = { plans: '任务计划', crons: '定时任务', executions: '任务执行记录' }
const STATE_KEYS = { plans: ['plans', 'planId'], crons: ['cron', 'cronId'], executions: ['executions', 'executionId'] }

/** Preview every eligible item across pages; submit only that confirmed snapshot. */
export function taskCleanupButton(category) {
  const ctx = Current
  let pending = false
  const button = h('button.btn.btn-outline.btn-sm', {
    'aria-label': `清理已完成的${LABELS[category]}`,
    onclick: async () => {
      if (pending || !ctx.active || ctx.busy) return
      pending = true
      button.disabled = true
      try {
        const preview = await Api.previewTaskCleanup(ctx.user, category, ctx.sessionId)
        if (!ctx.active || Current !== ctx) return
        if (!preview.count) {
          toast('没有可清理的已完成项目', { kind: 'info' })
          return
        }
        const detail = h('div.task-cleanup-list', {
          role: 'list', 'aria-label': '待清理项目',
        }, preview.items.map(item => h('div', { role: 'listitem' },
          h('div', item.title), h('div.faint', item.id))))
        const description = {
          plans: `将删除 ${preview.count} 个已完成计划及所属步骤、修订记录（含其他分页），无法恢复。独立定时任务和运行日志保留；不会处理运行中、暂停、失败或未完成的计划。`,
          crons: `将删除 ${preview.count} 个已完成的定时任务定义（含其他分页），无法恢复。任务计划和原始运行日志保留；不会处理启用中、运行中、暂停或失败的任务。`,
          executions: `将从任务中枢移除 ${preview.count} 条已完成记录（含其他分页）。原始运行日志及计划步骤结果保留，不影响后续执行。`,
        }[category]
        const confirmed = await Overlay.confirm({
          title: `清理已完成的${LABELS[category]}`, description, detail,
          confirmLabel: `确认清理 ${preview.count} 项`, tone: 'danger',
        })
        if (!confirmed || !ctx.active || Current !== ctx) return
        let result
        await mutate(async () => {
          result = await Api.cleanupCompletedTasks(ctx.user, category,
            preview.items.map(({ id, version }) => ({ id, version })), ctx.sessionId)
        }, null, () => {
          const [paging, selection] = STATE_KEYS[category]
          if (ctx.viewState[paging]) ctx.viewState[paging].page = 1
          delete ctx.viewState[selection]
          const issues = result.skipped.length + result.errors.length
          toast(`已清理 ${result.removed_count} 项${issues ? `；${issues} 项已变化或未清理，请刷新后重试` : ''}`,
            { kind: issues ? 'warning' : 'success', ttl: issues ? 7000 : 3000 })
        })
      } catch (error) {
        if (ctx.active) toast(error instanceof Error ? error.message : '清理失败，请重试',
          { kind: 'error', ttl: 7000 })
      } finally {
        pending = false
        button.disabled = false
      }
    },
  }, h('span', { html: icon('trash', 'icon-sm') }), '清理已完成')
  return button
}
