import { t as tr, useLanguage } from '../i18n'
import { Fragment, Suspense, lazy } from 'react'
import { Check, ChevronDown, Copy, ListChecks, Pencil, RotateCcw, Save, Trash2, Zap } from 'lucide-react'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import { AgentComposer } from '../components/AgentComposer'
import { KemoLogo } from '../components/KemoLogo'
import { ComposerReasoningSelect } from '../components/ComposerReasoningSelect'
import { ComposerModelSelect } from '../components/ComposerModelSelect'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { copyableAssistantText, type InlineWidgetAction } from '../components/Chat/inlineWidgetProtocol'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { ComposerMessagePanel } from '../components/ComposerMessagePanel'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { LongTaskBubble } from '../components/LongTaskBubble'
import { SubagentToolCard } from '../components/SubagentProgressBubble'
import { statusLabel } from '../components/ModuleUi'
import type { ScheduledTaskItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import { UserMessageNavigator, type UserMessageMarker } from '../components/UserMessageNavigator'
import type { ChatItem, PlanSummary } from '../types/api'
import type { PendingUploadedFile } from '../store/chatDrafts'
import {
  compactPlanAssistantText,
  partitionAssistantTurnItems,
  type ConversationBlock,
} from './chatState'
import {
  ContextCompressionBubble,
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
import { HISTORY_PAGE_SIZE, shouldShowLongTaskBubble } from './chatRunSupport'
import { FollowUpQueue } from './FollowUpQueue'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

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
    showWelcome, conversationMenuOpen, scrollRef, handleChatScroll, sessionId, historyQuery,
    user, overview, reasoningControl, activePlan, dockedPlan, activeTaskOpen, setActiveTaskOpen, navigate, setDraft,
    recentTasks, tokenTotal, tokenCacheRate, outputTokens, liveItems, items, historyData, conversationBlocks, userAvatarUrl,
    editedSources, running, lastUserMessage, editAndResend, conversationBusy, copyMessage, copiedItem,
    resolvePlan, revealPlan, showFollowOutput, resumeFollowingOutput, userMessageMarkers, totalRounds,
    loadEarlierHistory, jumpToUserMessage, runRetryNotice, setRunErrorNotice, runErrorNotice,
    activeCompression, longTaskQuery, longTaskBusy, stopLongTask, composerPlanDockRef, collapsedPlans,
    planActions, guidancePreviewItem, followUpQueue, queueFollowUp, guideDraft, reorderNextTurnMessages, liveSessionId, removeNextTurnMessage,
    setNextTurnMessageStatus, setConversationMenuOpen, draft, stopping, currentRound, roundLimit, pendingUploads, uploading,
    uploadFeedback, setUploadFeedback, setPendingUploads, editingSource, cancelEditAndResend,
    saveAndNewConversation, clearConversation, compressCurrentConversation, hasCommitted,
    toggleLongTask, regenerateLastResponse, conversationFeedback, uploadFiles, setCapabilityDrawerOpen,
    setKnowledgeDrawerOpen, openCommandPanel, sendGuidance, send, stopCurrentRun, knowledgeDrawerOpen,
    knowledgeQuery, referenceKnowledge, capabilityDrawerOpen, capabilityItems, expandsQuery, skillsQuery,
    referenceCapability,
  } = props
  const visibleFollowUpCount = followUpQueue.filter((message: { status: string }) => message.status !== 'guiding').length
  const contextSnapshot = overview?.context_snapshot?.available ? overview.context_snapshot : null
  const contextStats = contextSnapshot || overview?.context
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
  const handleWidgetAction = (action: InlineWidgetAction) => {
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

return (
  <div className={`chat-layout${showWelcome ? ' welcome-mode' : ''}${conversationMenuOpen ? ' conversation-menu-open' : ''}`}>
    <div className="chat-scroll-stage">
      <div className="chat-scroll" ref={scrollRef} onScroll={handleChatScroll}>
      {showWelcome && (!sessionId || !historyQuery.isLoading) && (
        <section className="welcome"><div className="welcome-inner">
          <div className="welcome-hero">
            <div className="welcome-mark"><KemoLogo /></div>
            <h1 className="welcome-hi">{greetingLabel()}，<span className="welcome-user">{user || tr('用户')}</span></h1>
            <p className="welcome-sub">kemo-agent 已就绪。描述目标即可开始，需要计划的任务会先生成草案再等确认。</p>
          </div>
          {activePlan && !dockedPlan && <div className="card card-pad">
            <div className="section-title"><ReferenceIcon name="tasks" small />{activePlan.title}<span className="badge">{statusLabel(activePlan.status)}</span></div>
            <p className="page-desc">{activePlan.description}</p>
            <div className="progress-track"><div className="progress-fill" style={{ width: `${activePlan.progress.percent}%` }} /></div>
            <div className="toolbar"><button className="btn btn-ghost btn-sm" onClick={() => setActiveTaskOpen((value: boolean) => !value)}>{activeTaskOpen ? '收起步骤' : '展开步骤'}</button>
              <button className="btn btn-outline btn-sm" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&session=${encodeURIComponent(sessionId)}`)}>{tr("任务中枢")}</button></div>
            {activeTaskOpen && activePlan.steps.slice(0, 6).map((step: PlanSummary['steps'][number]) => <div className="list-row" key={step.step_id}>{step.title}<span className="badge">{statusLabel(step.status)}</span></div>)}
          </div>}
          <div className="welcome-cols">
            <div className="welcome-section">
              <div className="welcome-section-head"><div className="welcome-section-title"><ReferenceIcon name="clock" small />近期活动</div>
                <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&session=${encodeURIComponent(sessionId)}`)}>{tr("任务中枢")}</button></div>
              <div className="reference-recent-list">{recentTasks.slice(0, 3).map(task => <button className="recent-task" key={task.id} onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}&tab=cron`)}>
                <div className="entry-icon"><ReferenceIcon name="clock" small /></div><div className="reference-entry-copy"><div className="rt-title truncate">{task.title}</div><div className="rt-sub">{task.schedule} · {task.nextRun}</div></div><span className="badge">{statusLabel(task.status)}</span>
              </button>)}
              {!recentTasks.length && <div className="empty"><div className="empty-title">暂无待执行的定时任务</div></div>}</div>
            </div>
            <div className="welcome-section">
              <div className="welcome-section-head"><div className="welcome-section-title"><ReferenceIcon name="activity" small />系统状态</div></div>
              <div className="welcome-metrics-panel" aria-label="Token 统计">
                <div className="welcome-metric">
                  <span className="welcome-metric-label">总 Token</span>
                  <strong>{tokenLabel(tokenTotal)}</strong>
                  <small>今日累计</small>
                </div>
                <div className="welcome-metric">
                  <span className="welcome-metric-label">Token 命中率</span>
                  <strong>{tokenRateLabel(tokenCacheRate)}</strong>
                  <small>缓存命中</small>
                </div>
                <div className="welcome-metric">
                  <span className="welcome-metric-label">输出 Token</span>
                  <strong>{tokenLabel(outputTokens)}</strong>
                  <small>今日输出</small>
                </div>
              </div>
            </div>
          </div>
        </div></section>
      )}
      {historyQuery.isLoading && <div className="center-state">正在加载历史…</div>}
      {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">历史读取失败，请刷新后重试；不会把读取错误当作空会话。</div>}
      <div className={`chat-stream ${items.length ? 'show' : ''}`}>
        {historyQuery.isFetchingNextPage ? <div className="history-page-status">正在加载更早对话…</div> : null}
        {historyQuery.hasNextPage && !historyQuery.isFetchingNextPage ? (
          <button className="history-page-button" type="button" onClick={() => { void loadEarlierHistory() }}>加载更早对话</button>
        ) : null}
        {!historyQuery.hasNextPage
          && (historyData?.pagination?.total_rounds ?? 0) > HISTORY_PAGE_SIZE
          ? <div className="history-page-status complete">已到达对话开头</div>
          : null}
        {items.length ? <div className="conversation-divider"><span>当前对话</span></div> : null}
        {conversationBlocks.map((block) => {
          if (block.kind === 'user') {
            const item = block.item
            return (
              <Fragment key={block.id}>
                <article className="msg is-user" data-user-message-id={item.id}>
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
                    {item.edited ? <span className="edited-label">编辑后重发</span> : null}
                    {editedSources.has(item.id) ? <span className="edited-label">已用于重发</span> : null}
                    {!running && item.content && lastUserMessage?.id === item.id ? <button type="button" onClick={() => void editAndResend(item.id, item.content)} disabled={Boolean(conversationBusy)} aria-label="编辑后重发"><Pencil size={12} />{conversationBusy === 'edit' ? '正在撤销…' : '编辑重发'}</button> : null}
                    <button type="button" onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label="复制消息">{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? '已复制' : tr('复制')}</button>
                  </div>
                </article>
              </Fragment>
            )
          }

          const { assistantMessages, usageItems, planItems, finalizedGuidance } = partitionAssistantTurnItems(block.items)
          const assistantText = assistantMessages.map((item) => copyableAssistantText(item.content)).filter(Boolean).join('\n\n')
          const assistantCopyId = assistantMessages.at(-1)?.id || block.id
          const hasPlanBubble = block.items.some((item) => item.kind === 'task_plan')
          return (
            <article key={block.id} className="msg is-assistant">
              <span className="msg-avatar is-assistant" title="kemo-agent"><KemoLogo /></span>
              <div className="msg-body">
                {block.items.map((item) => {
                  if (item.kind === 'context_compression' || item.kind === 'subagent_progress') return null
                  if (item.kind === 'long_task_boundary') return <div className="long-task-boundary" key={item.id}>长任务自动续跑 · 第 {item.continuation + 1} Run</div>
                  if (item.kind === 'retry_boundary') {
                    return <div className={`retry-boundary ${item.phase}`} key={item.id}>
                      {item.phase === 'snapshot'
                        ? `第 ${item.attempt} 次尝试未完成 · 已保留快照`
                        : `第 ${item.attempt} 次尝试继续执行`}
                    </div>
                  }
                  if (item.kind === 'reasoning') return <ReasoningTrace key={item.id} item={item} />
                  if (item.kind === 'execution_marker') return null
                  if (item.kind === 'tool') return item.name === 'subagent_dispatch'
                    ? <SubagentToolCard key={item.id} item={item} user={user} sessionId={sessionId} />
                    : <ToolCallCard key={item.id} item={item} user={user} sessionId={sessionId} />
                  if (item.kind === 'media') return <MediaArtifactCard key={item.id} user={user} artifact={item.artifact} />
                  if (item.kind === 'usage') return null
                  if (item.kind === 'task_plan') return null
                  if (item.kind === 'guidance') return null
                  if (item.kind === 'error') return <div key={item.id} className="chat-error">{item.content}</div>
                  if (item.role !== 'assistant') return null
                  return (
                    <div key={item.id} className="assistant-response">
                      <div className="bubble">
                        <Suspense fallback={<PlainTextMessage content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)} />}>
                          <MarkdownMessage
                            content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)}
                            streaming={Boolean(item.streaming)}
                            onWidgetAction={handleWidgetAction}
                          />
                        </Suspense>
                      </div>
                    </div>
                  )
                })}
                {(usageItems.length > 0 || assistantMessages.length > 0) && (
                  <div className="assistant-turn-footer">
                    <div className="assistant-turn-usage">{usageItems.map((item) => <UsageCard key={item.id} item={item} />)}</div>
                    {assistantMessages.length > 0 && (
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
                    )}
                    {assistantMessages.length > 0 && (
                      <button className="assistant-turn-copy" onClick={() => void copyMessage(assistantCopyId, assistantText)} disabled={!assistantText} aria-label="复制智能体回复">
                        {copiedItem === assistantCopyId ? <Check size={13} /> : <Copy size={13} />}{copiedItem === assistantCopyId ? '已复制' : tr('复制')}
                      </button>
                    )}
                  </div>
                )}
                {planItems.map((item) => {
                  const plan = resolvePlan(item.plan)
                  return <TaskPlanRecord key={item.id} plan={plan} docked={plan.plan_id === dockedPlan?.plan_id} onOpen={() => revealPlan(plan)} />
                })}
                {finalizedGuidance.length > 0 && <div className="assistant-guidance-list">{finalizedGuidance.map((item) => <GuidanceMessage key={item.id} user={user} item={item} placement="completed" />)}</div>}
              </div>
            </article>
          )
        })}
      </div>
      {showFollowOutput && items.length > 0 ? <button className="chat-follow-output" type="button" onClick={resumeFollowingOutput}><ChevronDown size={15} />继续跟随最新回复</button> : null}
      </div>
      <UserMessageNavigator
        markers={userMessageMarkers}
        scrollContainerRef={scrollRef}
        totalRounds={Math.max(totalRounds, historyData?.pagination?.total_rounds ?? 0)}
        hasEarlierMessages={Boolean(historyQuery.hasNextPage)}
        loadingEarlierMessages={historyQuery.isFetchingNextPage}
        onLoadEarlierMessages={loadEarlierHistory}
        onNavigate={jumpToUserMessage}
      />
    </div>
    <div className="composer-dock composer-zone">
      <div className="composer-auxiliary">
      {runRetryNotice ? (
        <div className="run-retry-bubble" role="status" aria-live="polite">
          <span>运行出现问题，正在自动重试（本次错误第 {runRetryNotice.nextAttempt}/{runRetryNotice.maxAttempts} 次）</span>
        </div>
      ) : null}
      {runErrorNotice ? (
        <div className="run-error-bubble" role="alert">
          <span>{runErrorNotice.message}</span>
          <button type="button" onClick={() => setRunErrorNotice(null)} aria-label="关闭运行错误提示">×</button>
        </div>
      ) : null}
      {running && activeCompression ? <ContextCompressionBubble item={activeCompression} /> : null}
      {longTaskQuery.data?.long_task && shouldShowLongTaskBubble(longTaskQuery.data.long_task.status) ? (
        <LongTaskBubble state={longTaskQuery.data.long_task} stopping={longTaskBusy} onCancel={() => { void stopLongTask() }} />
      ) : null}
      {dockedPlan ? (
        <div className="composer-plan-dock" ref={composerPlanDockRef}>
          <TaskPlanBubble
            {...taskPlanFromSummary(dockedPlan)}
            collapsed={collapsedPlans.has(dockedPlan.plan_id)}
            {...planActions(dockedPlan)}
          />
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
      <AgentComposer
        modelBar={<div className="composer-modelbar">
          <ComposerModelSelect key={`${user}:${overview?.provider?.type}:${overview?.provider?.base_url}`}
            user={user} provider={overview?.provider} disabled={running || stopping || conversationBusy} />
          <ComposerReasoningSelect key={`${user}:${overview?.provider?.model}`} control={reasoningControl} />
          <button
            className={`model-pill long-task-toggle${longTaskQuery.data?.long_task.enabled ? ' is-enabled' : ''}`}
            disabled={longTaskBusy}
            onClick={() => void toggleLongTask()}
            aria-label="切换长任务"
          >
            <Zap
              size={14}
              aria-hidden="true"
              fill={longTaskQuery.data?.long_task.enabled ? 'currentColor' : 'none'}
              strokeWidth={longTaskQuery.data?.long_task.enabled ? 2.2 : 1.8}
            />
            <span>长任务 · {longTaskQuery.data?.long_task.enabled ? '开' : '关'}</span>
          </button>
        </div>}
        value={draft}
        placeholder={user ? running ? 'Enter 加入消息跟进；Ctrl+Enter 直接本轮引导…' : '给 kemo-agent 发送消息…' : tr('请先选择用户')}
        currentRound={currentRound}
        totalRounds={totalRounds}
        roundLimit={roundLimit}
        contextPercent={contextStats?.percent}
        contextUsedTokens={contextSnapshot?.total_tokens ?? contextStats?.usage?.total_tokens}
        contextTokenWindow={contextSnapshot?.capacity_tokens ?? contextStats?.limit}
        running={running}
        stopping={stopping}
        disabled={!user}
        conversationMenuOpen={conversationMenuOpen}
        pendingFileCount={pendingUploads.length}
        uploading={uploading}
        uploadFeedback={<>
          {uploadFeedback ? <div className={`upload-feedback ${uploadFeedback.tone}`} role="status"><span>{uploadFeedback.text}</span><button type="button" onClick={() => setUploadFeedback(null)} aria-label="关闭上传提示">×</button></div> : null}
            {pendingUploads.length ? <PendingAttachmentTray user={user} files={pendingUploads} onRemove={(index: number) => setPendingUploads((current: PendingUploadedFile[]) => current.filter((_: PendingUploadedFile, currentIndex: number) => currentIndex !== index))} /> : null}
        </>}
        notice={editingSource ? <div className="edit-resend-banner"><span>最新一轮已撤销；修改内容后发送将创建新的最新一轮。</span><button onClick={cancelEditAndResend}>取消编辑</button></div> : null}
        conversationMenu={conversationMenuOpen ? (
          <div className="conversation-menu show" role="menu">
            <div className="conversation-menu-head">{tr("对话操作")}</div>
            <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void saveAndNewConversation() }}>
              <span className="conversation-action-icon"><Save size={16} /></span>
                <span className="conversation-action-copy"><strong>保存此对话，创建新对话</strong><span>{conversationBusy === 'save' ? '正在保存归档并切换…' : '保留当前归档，记忆转入后台提取'}</span></span>
            </button>
            <button className="conversation-action danger" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void clearConversation() }}>
              <span className="conversation-action-icon"><Trash2 size={16} /></span>
              <span className="conversation-action-copy"><strong>清空此对话</strong><span>{conversationBusy === 'clear' ? '正在删除当前归档…' : '删除当前归档并创建新对话'}</span></span>
            </button>
            <button className="conversation-action compress" role="menuitem" disabled={running || Boolean(conversationBusy) || !sessionId || !hasCommitted} onClick={() => { void compressCurrentConversation() }}>
              <span className="conversation-action-icon"><Zap size={16} /></span>
              <span className="conversation-action-copy"><strong>手动进行一次上下文压缩</strong><span>{conversationBusy === 'compress' ? '正在整理上下文…' : '优先按轮数压缩，不足时按 Token 压缩；记忆转入后台'}</span></span>
            </button>
            {conversationFeedback ? <div className={`conversation-menu-status ${conversationFeedback.tone}`} role="status">{conversationFeedback.text}</div> : null}
            <div className="conversation-menu-foot">再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。</div>
          </div>
        ) : null}
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
        onOpenCommands={openCommandPanel}
          onToggleConversationMenu={() => setConversationMenuOpen((value: boolean) => !value)}
        onSubmit={() => { if (running || stopping) queueFollowUp(); else void send(undefined, { uploadedFiles: pendingUploads }) }}
        onGuide={guideDraft}
        onNextTurn={queueFollowUp}
        onStop={() => { void stopCurrentRun() }}
      />
      <div className="composer-hint"><span><kbd>Enter</kbd> {running ? '下一轮跟进' : tr('发送')}</span><span><kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span><span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> 引导当前轮</span><span><kbd>Ctrl</kbd> + <kbd>K</kbd> {tr("命令面板")}</span></div>
    </div>
    <KnowledgeReferenceDrawer
      open={knowledgeDrawerOpen}
      documents={knowledgeQuery.data?.documents ?? []}
      categories={knowledgeQuery.data?.categories}
      loading={knowledgeQuery.isLoading || knowledgeQuery.isFetching}
      error={knowledgeQuery.isError}
      onClose={() => setKnowledgeDrawerOpen(false)}
      onReference={referenceKnowledge}
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
