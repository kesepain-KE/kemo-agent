import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronRight, RefreshCw } from 'lucide-react'
import { getAgentTask, getAgentTasks, type AgentTask } from '../../api/workspace'
import { t, useLanguage } from '../../i18n'
import { openWorkspaceAgentTask, openWorkspaceAgents, openWorkspacePath } from '../../store/workspace'
import { formatDateTime } from '../ModuleUi'
import { AgentTimeline } from './AgentTimeline'
import { AgentTaskResult } from './AgentTaskResult'
import { AgentUsage } from './AgentUsage'
import styles from './Workspace.module.css'

const labels: Record<string, string> = {
  queued: '排队中', running: '执行中', waiting_children: '等待子任务', merging: '汇总结果',
  completed: '已完成', failed: '失败', cancelling: '取消收尾中', cancelled: '已取消',
  interrupted: '已中断', timed_out: '超时停止', timed_out_running: '超时停止中',
  tool_limit: '工具额度停止',
  model_request: '请求模型', tool_running: '执行工具', tool_finished: '工具已返回',
  preparing: '准备执行', retrying: '正在重试', validating: '校验结果',
  dispatching: '分派子任务',
  started: '开始执行', partial: '部分完成', blocked: '受阻',
  duplicate_reused: '复用已有结果', retry_reuse_blocked: '重试复用已阻止',
  result_too_large: '结果超出限制', temporarily_unavailable: '工具暂不可用',
  recovery_blocked: '恢复已阻止',
}
export function agentStatus(status: string) { return t(labels[status] || status) }
function Badge({ task }: { task: AgentTask }) {
  return <span className={styles.badge} data-active={!['completed', 'failed', 'cancelled', 'interrupted', 'timed_out', 'tool_limit'].includes(task.status) || task.execution_live}>
    {agentStatus(task.status)}
  </span>
}
export function AgentTasks({ user, sessionId, active }: { user: string; sessionId: string; active: boolean }) {
  useLanguage()
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [sessionId])
  const query = useQuery({
    queryKey: ['workspace-agents', user, sessionId, page],
    queryFn: () => getAgentTasks(user, sessionId, page), enabled: active && Boolean(user && sessionId),
    retry: false, refetchInterval: active ? 1500 : false,
  })
  return <>
    <div className={styles.toolbar}><Bot size={16} /><strong>{t('智能体任务')}</strong>
      <span className={styles.status}>{t('当前对话')} · {query.data?.total ?? 0}</span>
      <button className={styles.icon} aria-label={t('刷新任务')} onClick={() => void query.refetch()} disabled={!sessionId || query.isFetching}><RefreshCw size={15} /></button>
    </div>
    <p className={styles.hint}>{t('后台提交不代表完成；必要子任务退出并由主智能体处理结果后，主任务才会结束。关闭标签不会停止任务。')}</p>
    {query.isError && <div className={styles.notice} role="alert">{query.error.message}</div>}
    <div className={`${styles.list} ${styles.agentList}`}>
      {(!sessionId || query.data?.tasks.length === 0) && <div className={styles.empty}><Bot size={28} />{t('暂无智能体任务')}</div>}
      {query.isPending && active && sessionId && <p className={styles.hint}>{t('正在读取任务…')}</p>}
      {query.data?.tasks.map(root => <section className={styles.agentGroup} key={root.id} aria-label={`${t('主任务')} · ${root.id}`}>
        <div className={styles.agentHeader}>
          <div className={styles.agentHeading}><h3>{t('主任务')}</h3><code title={root.id}>#{root.id.slice(-6)}</code><Badge task={root} /><time dateTime={root.created_at}>{formatDateTime(root.created_at)}</time></div>
          <p className={styles.agentDescription}>{root.description || root.id}</p>
          {root.status === 'running' && root.phase && <p className={styles.agentPhase}>{agentStatus(root.phase)}{root.active_tool ? ` · ${root.active_tool}` : ''}</p>}
        </div>
        {Boolean(root.children?.length || root.children_total) && <div className={styles.agentChildren} role="group" aria-label={t('子智能体')}>
          <div className={styles.agentChildrenHeading}><span>{t('子智能体')}</span><span>{root.children?.length ?? 0}{(root.children_total ?? 0) > (root.children?.length ?? 0) ? ` / ${root.children_total}` : ''}</span></div>
          {root.children?.map((child, index) => <button key={child.id} className={`${styles.job} ${styles.agentJob}`} onClick={() => openWorkspaceAgentTask(user, child)}>
            <span className={styles.jobTitle}><Bot size={15} /><span>#{child.submission_order ?? index + 1}</span><code>{child.agent}</code><Badge task={child} /><ChevronRight size={14} /></span>
            <span className={styles.jobCopy}>{child.description}</span>
            <span className={styles.jobCopy}>{child.handled ? t('主智能体已处理结果') : child.finished_at ? t('等待主智能体处理结果') : t('必要依赖')}</span>
          </button>)}
        </div>}
      </section>)}
    </div>
    {(query.data?.total_pages ?? 1) > 1 && <div className={styles.pager}>
      <button className={styles.button} disabled={page === 1} onClick={() => setPage(page - 1)}>{t('上一页')}</button>
      <span>{page} / {query.data?.total_pages}</span>
      <button className={styles.button} disabled={page >= (query.data?.total_pages ?? 1)} onClick={() => setPage(page + 1)}>{t('下一页')}</button>
    </div>}
  </>
}
export function AgentTaskView({ user, taskId, source, sessionId, active }: {
  user: string; taskId: string; source: string; sessionId: string; active: boolean
}) {
  useLanguage()
  const query = useQuery({
    queryKey: ['workspace-agent', user, taskId, source, sessionId],
    queryFn: () => getAgentTask(user, taskId, source, sessionId),
    enabled: active && Boolean(user), retry: false,
    refetchInterval: query => active && query.state.status !== 'error'
      && (!query.state.data?.task.finished_at || query.state.data?.task.execution_live) ? 1500 : false,
  })
  const task = query.data?.task
  return <>
    <div className={styles.toolbar}><Bot size={16} /><strong>{task?.agent || t('子智能体')}</strong>
      {task && <Badge task={task} />}<button className={styles.button} onClick={() => openWorkspaceAgents(user)}>{t('任务总览')}</button>
      <button className={styles.icon} aria-label={t('刷新任务')} onClick={() => void query.refetch()}><RefreshCw size={15} /></button>
    </div>
    {query.isError && <div className={styles.notice} role="alert">{query.error.message}</div>}
    {query.isPending && active && <p className={styles.hint}>{t('正在读取任务…')}</p>}
    <div className={styles.agentDetail}>
      {task && <>
        <div className={styles.meta}>{task.submission_order != null && <span>#{task.submission_order}</span>}<span>{task.id}</span><span>{source} · {sessionId}</span>
          <span>{t('开始')}：{formatDateTime(task.started_at || task.created_at)}</span>
          {task.finished_at && <span>{t('结束')}：{formatDateTime(task.finished_at)}</span>}
          {task.run_count != null && <span>{t('执行轮次')}：{task.run_count}</span>}
        </div>
        {task.status === 'timed_out' && task.resumable && <p className={styles.hint}>{t('已保存进度，主智能体可继续此任务并重新计时。')}</p>}
        {task.status === 'tool_limit' && task.resumable && <p className={styles.hint}>{t('已保存进度，主智能体可继续此任务并分配新一段工具额度。')}</p>}
        {task.status === 'timed_out_running' && <p className={styles.hint}>{t('正在等待执行真实退出，暂不能继续任务。')}</p>}
        {task.budget && typeof task.budget.tool_calls_used === 'number' && <div className={styles.meta}>
          <span>{t('本段工具调用')}：{task.budget.tool_calls_used} / {task.budget.unlimited ? t('不限次数') : task.budget.max_tool_calls}</span>
          <span>{t('累计工具调用')}：{task.budget.total_tool_calls}</span>
          <span>{t('运行时长预算')}：{task.budget.timeout} s</span>
        </div>}
        <p className={styles.hint}>{task.description}</p>
        <h4>{t('执行时间线')}</h4>
        <p className={styles.hint}>{t('点击行为卡片查看脱敏后的调用参数与返回结果。不展示私有推理、系统提示词或凭据；最多保留最近 160 条事件。')}</p>
        <AgentTimeline key={task.id} events={task.events || []} statusLabel={agentStatus} />
        {task.error?.message && <div className={styles.notice}>{task.error.message}</div>}
        {task.result != null && <><h4>{t(task.status.startsWith('timed_out') || task.status === 'tool_limit' || task.status === 'failed' || task.status === 'cancelled' ? '已有结论' : '最终结果')}</h4><AgentTaskResult result={task.result} /></>}
        {Boolean(task.artifacts?.length) && <><h4>{t('文件产物')}</h4><div className={styles.toolbar}>
          {task.artifacts?.map(file => <button key={`${file.scope}:${file.relative_path}`} className={styles.button}
            onClick={() => openWorkspacePath(user, file.scope, file.relative_path, file.name, file.size)}>{file.name}</button>)}
        </div></>}
        {task.usage && <AgentUsage usage={task.usage} />}
      </>}
    </div>
  </>
}
