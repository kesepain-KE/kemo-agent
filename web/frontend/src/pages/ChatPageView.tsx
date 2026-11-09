import { t as tr, useLanguage } from '../i18n'
import { Fragment, Suspense, lazy, memo, useCallback, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronUp, Copy, ListChecks, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import { AgentComposer } from '../components/AgentComposer'
import { ComposerContextUsage } from '../components/ComposerContextUsage'
import { KemoLogo } from '../components/KemoLogo'
import { ComposerReasoningSelect } from '../components/ComposerReasoningSelect'
import { ComposerModelSelect } from '../components/ComposerModelSelect'
import taskPlanStyles from '../components/TaskPlanBubble.module.css'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { copyableAssistantText, type InlineWidgetAction } from '../components/Chat/inlineWidgetProtocol'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { ComposerMessagePanel } from '../components/ComposerMessagePanel'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { SubagentToolCard } from '../components/SubagentProgressBubble'
import { statusLabel } from '../components/ModuleUi'
import type { ScheduledTaskItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import { UserDecisionCards } from '../components/UserDecisionCards'
import { UserMessageNavigator, type UserMessageMarker } from '../components/UserMessageNavigator'
import type { ChatItem, PlanSummary } from '../types/api'
import type { PendingUploadedFile } from '../store/chatDrafts'
import {
  compactPlanAssistantText,
  partitionAssistantTurnItems,
  type ConversationBlock,
} from './chatState'
import {
  greetingLabel,
  GuidanceMessage,
  guidanceDetailLabel,
  guidanceTitleLabel,
  MediaArtifactCard,
  PendingAttachmentTray,
  TaskPlanRecord,
  UserAttachmentCard,
  UserMessageAvatar,
} from './chatPresentation'
import { HISTORY_PAGE_SIZE } from './chatRunSupport'
import { FollowUpQueue } from './FollowUpQueue'
import { stripBackendJudgmentAsides } from './assistantOutput'
import { CONVERSATION_COMMAND_EVENT, type ConversationCommandAction } from '../components/appShellConfig'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

type AssistantTurnBlock = Extract<ConversationBlock, { kind: 'assistant' }>
type AssistantMessageItem = Extract<ChatItem, { kind: 'message' }>

function sameItemSequence(left: ChatItem[], right: ChatItem[]) {
  if (left === right) return true
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}

interface AssistantTurnProps {
  block: AssistantTurnBlock
  user: string
  sessionId: string
  dockedPlanId?: string
  running: boolean
  conversationBusy: string
  lastUserMessage: ChatItem | undefined
  copiedItem: string
  resolvePlan: (plan: PlanSummary) => PlanSummary
  revealPlan: (plan: PlanSummary) => void
  regenerateLastResponse: () => void | Promise<void>
  copyMessage: (id: string, content: string) => void | Promise<void>
  handleWidgetAction: (action: InlineWidgetAction) => void
}

export function assistantTurnPropsEqual(
  previous: Readonly<AssistantTurnProps>,
  next: Readonly<AssistantTurnProps>,
) {
  if (
    previous.user !== next.user
    || previous.block.id !== next.block.id
    || previous.sessionId !== next.sessionId
    || previous.dockedPlanId !== next.dockedPlanId
    || previous.running !== next.running
    || previous.conversationBusy !== next.conversationBusy
    || previous.lastUserMessage !== next.lastUserMessage
    || previous.copiedItem !== next.copiedItem
    || previous.resolvePlan !== next.resolvePlan
    || previous.revealPlan !== next.revealPlan
    || previous.regenerateLastResponse !== next.regenerateLastResponse
    || previous.copyMessage !== next.copyMessage
    || previous.handleWidgetAction !== next.handleWidgetAction
  ) return false
  return sameItemSequence(previous.block.items, next.block.items)
}

const AssistantTurnItem = memo(function AssistantTurnItem({
  item,
  user,
  sessionId,
  hasPlanBubble,
  handleWidgetAction,
}: {
  item: ChatItem
  user: string
  sessionId: string
  hasPlanBubble: boolean
  handleWidgetAction: (action: InlineWidgetAction) => void
}) {
  if (item.kind === 'context_compression' || item.kind === 'subagent_progress') return null
  if (item.kind === 'retry_boundary') {
    return <div className={`retry-boundary ${item.phase}`}>
      {item.phase === 'snapshot'
        ? `第 ${item.attempt} 次尝试未完成 · 已保留快照`
        : `第 ${item.attempt} 次尝试继续执行`}
    </div>
  }
  if (item.kind === 'reasoning') return <ReasoningTrace item={item} />
  if (item.kind === 'execution_marker') return null
  if (item.kind === 'tool') {
    return item.name === 'subagent_dispatch'
      ? <SubagentToolCard item={item} user={user} sessionId={sessionId} />
      : <ToolCallCard item={item} user={user} sessionId={sessionId} />
  }
  if (item.kind === 'media') return <MediaArtifactCard user={user} artifact={item.artifact} />
  if (item.kind === 'usage' || item.kind === 'task_plan' || item.kind === 'guidance') return null
  if (item.kind === 'error') return <div className="chat-error">{item.content}</div>
  if (item.role !== 'assistant') return null
  return (
    <AssistantResponse
      item={item}
      hasPlanBubble={hasPlanBubble}
      handleWidgetAction={handleWidgetAction}
    />
  )
})

const AssistantResponse = memo(function AssistantResponse({
  item,
  hasPlanBubble,
  handleWidgetAction,
}: {
  item: AssistantMessageItem
  hasPlanBubble: boolean
  handleWidgetAction: (action: InlineWidgetAction) => void
}) {
  const rawContent = item.content || (item.streaming ? '…' : '')
  const visibleContent = item.level === 'final' ? stripBackendJudgmentAsides(rawContent) : rawContent
  const content = compactPlanAssistantText(visibleContent, hasPlanBubble)
  return (
    <div className="assistant-response">
      <div className="bubble">
        <Suspense fallback={<PlainTextMessage content={content} />}>
          <MarkdownMessage
            content={content}
            streaming={Boolean(item.streaming)}
            onWidgetAction={handleWidgetAction}
          />
        </Suspense>
      </div>
    </div>
  )
})

export const AssistantTurn = memo(function AssistantTurn({
  block,
  user,
  sessionId,
  dockedPlanId,
  running,
  conversationBusy,
  lastUserMessage,
  copiedItem,
  resolvePlan,
  revealPlan,
  regenerateLastResponse,
  copyMessage,
  handleWidgetAction,
}: AssistantTurnProps) {
  useLanguage()
  const [processOpen, setProcessOpen] = useState(false)
  const processId = useId()
  const { assistantMessages, finalAssistantMessage, usageItems, planItems, finalizedGuidance } = useMemo(
    () => partitionAssistantTurnItems(block.items),
    [block.items],
  )
  // Widget fallback parsing is only needed when copying. Do not reparse every
  // completed response (or the growing live response) on each streamed delta.
  const hasAssistantText = assistantMessages.some((item) => Boolean(item.content))
  const copyAssistantText = () => {
    const text = assistantMessages.map((item) => copyableAssistantText(
      item.level === 'final' ? stripBackendJudgmentAsides(item.content) : item.content,
    )).filter(Boolean).join('\n\n')
    return copyMessage(assistantCopyId, text)
  }
  const assistantCopyId = assistantMessages.at(-1)?.id || block.id
  const hasPlanBubble = useMemo(
    () => block.items.some((item) => item.kind === 'task_plan'),
    [block.items],
  )
  const processItems = finalAssistantMessage
    ? block.items.filter((item) => item.id !== finalAssistantMessage.id && item.kind !== 'usage')
    : []
  const hasProcessContent = processItems.some((item) => {
    if (item.kind === 'message') return item.role === 'assistant' && Boolean(item.content || item.streaming)
    if (item.kind === 'reasoning') return Boolean(item.content || item.streaming)
    return item.kind === 'tool' || item.kind === 'media' || item.kind === 'error'
      || item.kind === 'retry_boundary'
  }) || planItems.length > 0 || finalizedGuidance.length > 0
  const footer = (usageItems.length > 0 || assistantMessages.length > 0) && (
    <div className="assistant-turn-footer">
      {usageItems.length > 0 && <div className="assistant-turn-usage">{usageItems.map((item) => <UsageCard key={item.id} item={item} />)}</div>}
      {assistantMessages.length > 0 && (
        <div className="assistant-turn-actions">
          <button
            type="button"
            className="assistant-turn-retry"
            onClick={() => void regenerateLastResponse()}
            disabled={running || Boolean(conversationBusy) || !lastUserMessage}
            aria-label="重试智能体回复"
            title="重试智能体回复"
          >
            <RotateCcw size={14} />
          </button>
          <button className="assistant-turn-copy" onClick={() => void copyAssistantText()} disabled={!hasAssistantText} aria-label="复制智能体回复">
            {copiedItem === assistantCopyId ? <Check size={13} /> : <Copy size={13} />}{copiedItem === assistantCopyId ? tr('已复制') : tr('复制')}
          </button>
        </div>
      )}
    </div>
  )
  const renderProcessItem = (item: ChatItem) => {
    if (item.kind === 'task_plan' && item.presentation !== 'reference') {
      const plan = resolvePlan(item.plan)
      return <TaskPlanRecord key={item.id} plan={plan} docked={plan.plan_id === dockedPlanId} onOpen={revealPlan} />
    }
    if (item.kind === 'guidance' && item.finalized) {
      return <GuidanceMessage key={item.id} user={user} item={item} placement="completed" />
    }
    return (
      <AssistantTurnItem
        key={item.id}
        item={item}
        user={user}
        sessionId={sessionId}
        hasPlanBubble={hasPlanBubble}
        handleWidgetAction={handleWidgetAction}
      />
    )
  }
  return (
    <article className="msg is-assistant">
      {finalAssistantMessage && hasProcessContent ? (
        <button
          type="button"
          className="msg-avatar is-assistant assistant-process-toggle"
          aria-label={processOpen ? tr('折叠处理过程') : tr('展开处理过程')}
          title={processOpen ? tr('折叠处理过程') : tr('展开处理过程')}
          aria-expanded={processOpen}
          aria-controls={processId}
          onClick={() => setProcessOpen((open) => !open)}
        >
          {processOpen ? <ChevronUp size={19} aria-hidden="true" /> : <KemoLogo />}
        </button>
      ) : (
        <span className="msg-avatar is-assistant" title="kemo-agent"><KemoLogo /></span>
      )}
      <div className="msg-body">
        {finalAssistantMessage ? (
          <>
            {hasProcessContent && (
              <div
                id={processId}
                className="assistant-processing"
                role="region"
                aria-label={tr('处理过程')}
                hidden={!processOpen}
              >
                <div className="assistant-processing-body">
                  {processItems.map(renderProcessItem)}
                </div>
              </div>
            )}
            <div className="assistant-turn-final">
              <AssistantTurnItem
                item={finalAssistantMessage}
                user={user}
                sessionId={sessionId}
                hasPlanBubble={hasPlanBubble}
                handleWidgetAction={handleWidgetAction}
              />
              {footer}
            </div>
          </>
        ) : (
          <>
            {block.items.map((item) => (
              <AssistantTurnItem
                key={item.id}
                item={item}
                user={user}
                sessionId={sessionId}
                hasPlanBubble={hasPlanBubble}
                handleWidgetAction={handleWidgetAction}
              />
            ))}
            {footer}
            {planItems.map((item) => {
              const plan = resolvePlan(item.plan)
              return <TaskPlanRecord key={item.id} plan={plan} docked={plan.plan_id === dockedPlanId} onOpen={revealPlan} />
            })}
            {finalizedGuidance.length > 0 && <div className="assistant-guidance-list">{finalizedGuidance.map((item) => <GuidanceMessage key={item.id} user={user} item={item} placement="completed" />)}</div>}
          </>
        )}
      </div>
    </article>
  )
}, assistantTurnPropsEqual)

interface ChatPageViewProps {
  [key: string]: any
  items: ChatItem[]
  liveItems: ChatItem[]
  conversationBlocks: ConversationBlock[]
  recentTasks: ScheduledTaskItem[]
  tokenTotal?: number
  tokenCacheRate?: number
  outputTokens?: number
  userMessageMarkers: UserMessageMarker[]
  pendingUploads: PendingUploadedFile[]
  capabilityItems: CapabilityReferenceItem[]
}

export function ChatPageView(props: ChatPageViewProps) {
  useLanguage()
  const {
    showWelcome, scrollRef, handleChatScroll, handleChatContentClick, sessionId, historyQuery,
    user, overview, reasoningControl, activePlan, dockedPlan, activeTaskOpen, setActiveTaskOpen, navigate, setDraft,
    recentTasks, tokenTotal, tokenCacheRate, outputTokens, liveItems, items, historyData, conversationBlocks, userAvatarUrl,
    editedSources, running, lastUserMessage, editAndResend, conversationBusy, conversationFeedback, setConversationFeedback, copyMessage, copiedItem,
    resolvePlan, revealPlan, showFollowOutput, resumeFollowingOutput, userMessageMarkers, totalRounds,
    navigatingMessage, jumpToUserMessage, runRetryNotice, setRunErrorNotice, runErrorNotice,
    taskPlanDockRef, collapsedPlans,
    planActions, guidancePreviewItem, followUpQueue, queueFollowUp, guideDraft, reorderNextTurnMessages, liveSessionId, removeNextTurnMessage,
    setNextTurnMessageStatus, draft, stopping, pendingUploads, uploading,
    uploadFeedback, setUploadFeedback, setPendingUploads, editingSource, cancelEditAndResend,
    regenerateLastResponse, uploadFiles, setCapabilityDrawerOpen,
    setKnowledgeDrawerOpen, sendGuidance, send, stopCurrentRun, knowledgeDrawerOpen,
    knowledgeQuery, referenceKnowledge, referenceKnowledgeDirectory, capabilityDrawerOpen, capabilityItems, expandsQuery, skillsQuery,
    referenceCapability,
  } = props
  const visibleFollowUpCount = followUpQueue.filter((message: { status: string }) => message.status !== 'guiding').length
  const overviewMatches = overview?.session_id === (sessionId || '')
  const tokenLabel = (value: number | undefined) => (
    typeof value === 'number' && Number.isFinite(value)
      ? new Intl.NumberFormat('en-US').format(Math.max(0, Math.round(value)))
      : '—'
  )
  const tokenRateLabel = (value: number | undefined) => (
    typeof value === 'number' && Number.isFinite(value)
      ? `${Math.max(0, value).toFixed(1)}%`
      : '—'
  )
  const copyMessageRef = useRef<(id: string, content: string) => void | Promise<void>>(copyMessage)
  const revealPlanRef = useRef<(plan: PlanSummary) => void>(revealPlan)
  const regenerateLastResponseRef = useRef<() => void | Promise<void>>(regenerateLastResponse)
  useLayoutEffect(() => {
    copyMessageRef.current = copyMessage
    revealPlanRef.current = revealPlan
    regenerateLastResponseRef.current = regenerateLastResponse
  }, [copyMessage, regenerateLastResponse, revealPlan])
  const stableCopyMessage = useCallback((id: string, content: string) => copyMessageRef.current(id, content), [])
  const stableRevealPlan = useCallback((plan: PlanSummary) => revealPlanRef.current(plan), [])
  const stableRegenerateLastResponse = useCallback(() => regenerateLastResponseRef.current(), [])
  const widgetActionRef = useRef<(action: InlineWidgetAction) => void>(() => {})
  useLayoutEffect(() => {
    widgetActionRef.current = (action: InlineWidgetAction) => {
    if (action.type === 'fill-input') {
      setDraft(action.text)
      window.requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')?.focus())
      return
    }
    if (action.type === 'copy') {
      void copyMessage('widget-action', action.text)
      return
    }
    if (running || stopping) {
      setDraft(action.text)
      window.requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')?.focus())
      return
    }
      void send(action.text)
    }
  })
  const handleWidgetAction = useCallback((action: InlineWidgetAction) => widgetActionRef.current(action), [])

return (
  <div className={`chat-layout${showWelcome ? ' welcome-mode' : ''}`}>
    <div className="chat-scroll-stage">
      <div className={taskPlanStyles.sideDock} ref={taskPlanDockRef}>
          {dockedPlan && <TaskPlanBubble
            className={taskPlanStyles.sideCard}
            {...taskPlanFromSummary(dockedPlan)}
            collapsed={collapsedPlans.has(dockedPlan.plan_id)}
            {...planActions(dockedPlan)}
          />}
          <UserDecisionCards key={`${user}:${sessionId}`} user={user} sessionId={sessionId || ''} running={running} />
      </div>
      <div className="chat-scroll" ref={scrollRef} onScroll={handleChatScroll} onClickCapture={handleChatContentClick}>
      {showWelcome && (!sessionId || !historyQuery.isLoading) && (
        <section className="welcome"><div className="welcome-inner">
          <div className="welcome-hero">
            <div className="welcome-mark"><KemoLogo /></div>
            <h1 className="welcome-hi">{greetingLabel()}，<span className="welcome-user">{user || tr('用户')}</span></h1>
            <p className="welcome-sub">{tr("kemo-agent 已就绪。描述目标即可开始，需要计划的任务会先生成草案再等确认。")}</p>
          </div>
          {activePlan && !dockedPlan && <div className="card card-pad">
            <div className="section-title"><ReferenceIcon name="tasks" small />{activePlan.title}<span className="badge">{statusLabel(activePlan.status)}</span></div>
            <p className="page-desc">{activePlan.description}</p>
            <div className="progress-track"><div className="progress-fill" style={{ width: `${activePlan.progress.percent}%` }} /></div>
            <div className="toolbar"><button className="btn btn-ghost btn-sm" onClick={() => setActiveTaskOpen((value: boolean) => !value)}>{activeTaskOpen ? tr('收起步骤') : tr('展开步骤')}</button>
              <button className="btn btn-outline btn-sm" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&session=${encodeURIComponent(sessionId)}`)}>{tr("任务中枢")}</button></div>
            {activeTaskOpen && activePlan.steps.slice(0, 6).map((step: PlanSummary['steps'][number]) => <div className="list-row" key={step.step_id}>{step.title}<span className="badge">{statusLabel(step.status)}</span></div>)}
          </div>}
          <div className="welcome-cols">
            <div className="welcome-section">
              <div className="welcome-section-head"><div className="welcome-section-title"><ReferenceIcon name="clock" small />{tr("近期活动")}</div>
                <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&session=${encodeURIComponent(sessionId)}`)}>{tr("任务中枢")}</button></div>
              <div className="reference-recent-list">{recentTasks.slice(0, 3).map(task => <button className="recent-task" key={task.id} onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&tab=cron`)}>
                <div className="entry-icon"><ReferenceIcon name="clock" small /></div><div className="reference-entry-copy"><div className="rt-title truncate">{task.title}</div><div className="rt-sub">{task.schedule} · {task.nextRun}</div></div><span className="badge">{statusLabel(task.status)}</span>
              </button>)}
              {!recentTasks.length && <div className="empty"><div className="empty-title">{tr("暂无待执行的定时任务")}</div></div>}</div>
            </div>
            <div className="welcome-section">
              <div className="welcome-section-head"><div className="welcome-section-title"><ReferenceIcon name="activity" small />{tr("系统状态")}</div></div>
              <div className="welcome-metrics-panel" aria-label={tr("Token 统计")}>
                <div className="welcome-metric">
                  <span className="welcome-metric-label">{tr("总 Token")}</span>
                  <strong>{tokenLabel(tokenTotal)}</strong>
                  <small>{tr("今日累计")}</small>
                </div>
                <div className="welcome-metric">
                  <span className="welcome-metric-label">{tr("Token 命中率")}</span>
                  <strong>{tokenRateLabel(tokenCacheRate)}</strong>
                  <small>{tr("缓存命中")}</small>
                </div>
                <div className="welcome-metric">
                  <span className="welcome-metric-label">{tr("输出 Token")}</span>
                  <strong>{tokenLabel(outputTokens)}</strong>
                  <small>{tr("今日输出")}</small>
                </div>
              </div>
            </div>
          </div>
        </div></section>
      )}
      {historyQuery.isLoading && <div className="center-state">{tr("正在加载历史…")}</div>}
      {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">{tr("历史读取失败，请刷新后重试；不会把读取错误当作空会话。")}</div>}
      <div className={`chat-stream ${items.length ? 'show' : ''}`}>
        {historyQuery.isFetchingNextPage ? <div className="history-page-status">{tr("正在加载更早对话…")}</div> : null}
        {!historyQuery.hasNextPage
          && (historyData?.pagination?.total_cards ?? 0) > HISTORY_PAGE_SIZE
          ? <div className="history-page-status complete">{tr("已到达对话开头")}</div>
          : null}
        {items.length ? <div className="conversation-divider"><span>{tr("当前对话")}</span></div> : null}
        {conversationBlocks.map((block) => {
          if (block.kind === 'user') {
            const item = block.item
            return (
              <Fragment key={block.id}>
                <article className="msg is-user" data-user-message-id={item.id}
                  data-integration-number={item.integrationNumber}>
                  <UserMessageAvatar avatarUrl={userAvatarUrl} user={user} />
                  <div className="msg-body">
                    {item.content ? <div className="bubble"><PlainTextMessage content={item.content} /></div> : null}
                    {item.attachments?.length ? (
                      <div className="user-attachment-list">
                        {item.attachments.map((attachment, index) => (
                          <UserAttachmentCard key={attachment.asset_id || `${attachment.name}_${index}`} user={user} attachment={attachment} />
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="user-message-actions">
                    {item.edited ? <span className="edited-label">{tr("编辑后重发")}</span> : null}
                    {editedSources.has(item.id) ? <span className="edited-label">{tr("已用于重发")}</span> : null}
                    {!running && item.content && lastUserMessage?.id === item.id ? <button type="button" onClick={() => void editAndResend(item.id, item.content)} disabled={Boolean(conversationBusy)} aria-label={tr("编辑后重发")}><Pencil size={12} />{conversationBusy === 'edit' ? tr('正在撤销…') : tr('编辑重发')}</button> : null}
                    <button type="button" onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label={tr("复制消息")}>{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? tr('已复制') : tr('复制')}</button>
                  </div>
                </article>
              </Fragment>
            )
          }

          return (
            <AssistantTurn
              key={block.id}
              block={block}
              user={user}
              sessionId={sessionId}
              dockedPlanId={dockedPlan?.plan_id}
              running={running}
              conversationBusy={conversationBusy || (editingSource ? 'edit' : '')}
              lastUserMessage={lastUserMessage}
              copiedItem={copiedItem}
              resolvePlan={resolvePlan}
              revealPlan={stableRevealPlan}
              regenerateLastResponse={stableRegenerateLastResponse}
              copyMessage={stableCopyMessage}
              handleWidgetAction={handleWidgetAction}
            />
          )
        })}
      </div>
      </div>
      <UserMessageNavigator
        markers={userMessageMarkers}
        scrollContainerRef={scrollRef}
        integrationCount={Math.max(totalRounds, historyData?.pagination?.integration_count ?? 0)}
        navigating={navigatingMessage}
        onNavigate={jumpToUserMessage}
      />
    </div>
    <div className="composer-dock composer-zone">
      <div className="composer-auxiliary">
      {conversationFeedback?.tone === 'error' ? (
        <div className="run-error-bubble" role="alert">
          <span>{conversationFeedback.text}</span>
          <button type="button" onClick={() => setConversationFeedback(null)} aria-label={tr('关闭')}>×</button>
        </div>
      ) : null}
      {runRetryNotice ? (
        <div className="run-retry-bubble" role="status" aria-live="polite">
          <span>运行出现问题，正在自动重试（本次错误第 {runRetryNotice.nextAttempt}/{runRetryNotice.maxAttempts} 次）</span>
        </div>
      ) : null}
      {runErrorNotice ? (
        <div className="run-error-bubble" role="alert">
          <span>{runErrorNotice.message}</span>
          <button type="button" onClick={() => setRunErrorNotice(null)} aria-label={tr("关闭运行错误提示")}>×</button>
        </div>
      ) : null}
      <ComposerMessagePanel
        sessionKey={liveSessionId || sessionId || ''}
        followUpCount={visibleFollowUpCount}
        followUpContent={<FollowUpQueue embedded user={user} messages={followUpQueue} canGuide={running && !stopping}
          onGuide={(message) => { void sendGuidance(message) }}
          onRemove={(id) => removeNextTurnMessage(user, liveSessionId, id)}
          onRetry={(id) => setNextTurnMessageStatus(user, liveSessionId, id, 'queued')}
          onReorder={(id, targetId) => reorderNextTurnMessages(user, liveSessionId, id, targetId)} />}
        guidanceContent={guidancePreviewItem ? <div className="composer-guidance-preview composer-guidance-preview-embedded" aria-live="polite"><GuidanceMessage user={user} item={guidancePreviewItem} placement="current" /></div> : undefined}
        guidanceLabel={guidancePreviewItem ? guidanceTitleLabel(guidancePreviewItem) : undefined}
        guidanceDetail={guidancePreviewItem?.status === 'accepted' ? guidanceDetailLabel(guidancePreviewItem) : undefined}
      />
      </div>
      <div className="composer-input-anchor">
      {showFollowOutput && items.length > 0 ? <button className="chat-follow-output" type="button" onClick={resumeFollowingOutput}><ChevronDown size={15} />{tr("继续跟随最新回复")}</button> : null}
      <AgentComposer
        modelBar={<div className="composer-modelbar">
          <ComposerModelSelect key={`${user}:${overview?.provider?.type}:${overview?.provider?.base_url}`}
            user={user} provider={overview?.provider} disabled={running || stopping || conversationBusy} />
          <ComposerReasoningSelect key={`${user}:${overview?.provider?.model}`} control={reasoningControl} />
        </div>}
        value={draft}
        placeholder={user ? running ? tr('Enter 加入消息跟进；Ctrl+Enter 直接本轮引导…') : tr('给 kemo-agent 发送消息…') : tr('请先选择用户')}
        contextUsage={<ComposerContextUsage key={`${user}:${sessionId}`}
          snapshot={overviewMatches ? overview.context_snapshot : undefined}
          capacityTokens={overviewMatches ? overview.context.limit : undefined}
          cacheHitRate={overviewMatches && sessionId ? overview.context.usage.cache_hit_rate : undefined} />}
        running={running}
        stopping={stopping}
        disabled={!user || conversationBusy === 'edit'}
        commandBusy={Boolean(conversationBusy)}
        pendingFileCount={pendingUploads.length}
        uploading={uploading}
        uploadFeedback={<>
          {uploadFeedback ? <div className={`upload-feedback ${uploadFeedback.tone}`} role="status"><span>{uploadFeedback.text}</span><button type="button" onClick={() => setUploadFeedback(null)} aria-label={tr("关闭上传提示")}>×</button></div> : null}
            {pendingUploads.length ? <PendingAttachmentTray user={user} files={pendingUploads} onRemove={(index: number) => setPendingUploads((current: PendingUploadedFile[]) => current.filter((_: PendingUploadedFile, currentIndex: number) => currentIndex !== index))} /> : null}
        </>}
        notice={editingSource ? <div className="edit-resend-banner"><span>{tr('修改后发送将替换最新一轮；取消编辑保留原对话。')}</span><button onClick={cancelEditAndResend} disabled={Boolean(conversationBusy)}>{tr('取消编辑')}</button></div> : null}
        onChange={setDraft}
        onUploadFiles={uploadFiles}
        onOpenKnowledge={() => {
          setCapabilityDrawerOpen(false)
          setKnowledgeDrawerOpen(true)
        }}
        onOpenCapabilities={() => {
          setKnowledgeDrawerOpen(false)
          setCapabilityDrawerOpen(true)
        }}
        onSlashCommand={(action: ConversationCommandAction) => {
          window.dispatchEvent(new CustomEvent<ConversationCommandAction>(CONVERSATION_COMMAND_EVENT, { detail: action }))
        }}
        onSubmit={() => { if (running || stopping) queueFollowUp(); else void send(undefined, { uploadedFiles: pendingUploads }) }}
        onGuide={guideDraft}
        onStop={() => { void stopCurrentRun() }}
        onStopAndCancelSubagents={() => { void stopCurrentRun(true) }}
      />
      </div>
      <div className="composer-hint"><span><kbd>Enter</kbd> {running ? tr('下一轮跟进') : tr('发送')}</span><span><kbd>Shift</kbd> + <kbd>Enter</kbd> {tr("换行")}</span><span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> {tr("引导当前轮")}</span><span><kbd>Ctrl</kbd> + <kbd>K</kbd> {tr("命令面板")}</span></div>
    </div>
    <KnowledgeReferenceDrawer
      open={knowledgeDrawerOpen}
      user={user}
      roots={knowledgeQuery.data?.roots ?? []}
      directories={knowledgeQuery.data?.directories ?? []}
      documents={knowledgeQuery.data?.documents ?? []}
      loading={knowledgeQuery.isLoading || knowledgeQuery.isFetching}
      error={knowledgeQuery.isError}
      onClose={() => setKnowledgeDrawerOpen(false)}
      onReference={referenceKnowledge}
      onReferenceDirectory={referenceKnowledgeDirectory}
    />
    <CapabilityReferenceDrawer
      open={capabilityDrawerOpen}
      items={capabilityItems}
      loading={{
        expand: expandsQuery.isLoading || expandsQuery.isFetching,
        skill: skillsQuery.isLoading || skillsQuery.isFetching,
        plugin: skillsQuery.isLoading || skillsQuery.isFetching,
      }}
      error={{
        expand: expandsQuery.isError,
        skill: skillsQuery.isError,
        plugin: skillsQuery.isError,
      }}
      onClose={() => setCapabilityDrawerOpen(false)}
      onReference={referenceCapability}
    />
  </div>
)
}
