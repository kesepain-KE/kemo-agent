import { Fragment, Suspense, lazy } from 'react'
import { Check, ChevronDown, Copy, ListChecks, Pencil, RotateCcw, Save, Trash2, Workflow, Zap } from 'lucide-react'
import { AgentComposer } from '../components/AgentComposer'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { LongTaskBubble } from '../components/LongTaskBubble'
import { statusLabel } from '../components/ModuleUi'
import { RecentActivityCard, type ScheduledTaskItem, type SenseDataItem } from '../components/RecentActivityCard'
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
  MediaArtifactCard,
  PendingAttachmentTray,
  quickStartCards,
  TaskPlanRecord,
  UserAttachmentCard,
  UserMessageAvatar,
} from './chatPresentation'
import { HISTORY_PAGE_SIZE, shouldShowLongTaskBubble } from './chatRunSupport'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

interface ChatPageViewProps {
  [key: string]: any
  items: ChatItem[]
  liveItems: ChatItem[]
  conversationBlocks: ConversationBlock[]
  recentTasks: ScheduledTaskItem[]
  recentSenseData: SenseDataItem[]
  userMessageMarkers: UserMessageMarker[]
  pendingUploads: PendingUploadedFile[]
  capabilityItems: CapabilityReferenceItem[]
}

export function ChatPageView(props: ChatPageViewProps) {
  const {
    showWelcome, conversationMenuOpen, scrollRef, handleChatScroll, sessionId, historyQuery,
    user, overview, activePlan, dockedPlan, activeTaskOpen, setActiveTaskOpen, navigate, setDraft,
    recentTasks, recentSenseData, liveItems, items, historyData, conversationBlocks, userAvatarUrl,
    editedSources, running, lastUserMessage, editAndResend, conversationBusy, copyMessage, copiedItem,
    resolvePlan, revealPlan, showFollowOutput, resumeFollowingOutput, userMessageMarkers, totalRounds,
    loadEarlierHistory, jumpToUserMessage, runRetryNotice, setRunErrorNotice, runErrorNotice,
    activeCompression, longTaskQuery, longTaskBusy, stopLongTask, composerPlanDockRef, collapsedPlans,
    planActions, guidancePreviewItem, pendingNextTurn, liveSessionId, removeNextTurnMessage,
    setNextTurnMessageStatus, setConversationMenuOpen, draft, stopping, currentRound, roundLimit, pendingUploads, uploading,
    uploadFeedback, setUploadFeedback, setPendingUploads, editingSource, cancelEditAndResend,
    saveAndNewConversation, clearConversation, compressCurrentConversation, hasCommitted,
    toggleLongTask, regenerateLastResponse, conversationFeedback, uploadFiles, setCapabilityDrawerOpen,
    setKnowledgeDrawerOpen, openCommandPanel, sendGuidance, send, stopCurrentRun, knowledgeDrawerOpen,
    knowledgeQuery, referenceKnowledge, capabilityDrawerOpen, capabilityItems, expandsQuery, skillsQuery,
    referenceCapability,
  } = props

return (
  <div className={`view chat-view active${showWelcome ? ' welcome-mode' : ''}${conversationMenuOpen ? ' conversation-menu-open' : ''}`}>
    <div className="chat-scroll-stage">
      <div className="chat-scroll" ref={scrollRef} onScroll={handleChatScroll}>
      {showWelcome && (!sessionId || !historyQuery.isLoading) && (
        <section className="welcome">
          <div className="welcome-top">
            <article className="greeting-card">
              <div className="hero-logo"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent logo" /></div>
              <div className="greeting-copy">
                <h1>{greetingLabel()}，{user || '用户'}</h1>
                <p>当前用户的配置、历史、知识、任务与技能运行态已载入。今天需要处理什么？</p>
                <span className="role-line">● 当前用户 · users/{user || '—'}</span>
              </div>
            </article>
            <article className="snapshot-card">
              <div className="snapshot-item"><strong>{overview?.counts.sessions ?? '—'} 个</strong><span>Web 会话</span></div>
              <div className="snapshot-item"><strong className="ok">{overview?.counts.knowledge_documents ?? '—'} 项</strong><span>文件知识</span></div>
              <div className="snapshot-item"><strong>{overview?.counts.enabled_tools ?? '—'} 个</strong><span>可用工具</span></div>
              <div className="snapshot-item"><strong>{overview?.counts.active_tasks ?? '—'} 个</strong><span>活动任务</span></div>
            </article>
          </div>
          {activePlan && !dockedPlan && <article className={`active-task-card ${activeTaskOpen ? 'open' : ''}`}>
            <div className="active-task-main">
              <span className="active-task-play"><ListChecks size={17} /></span>
              <span className="active-task-copy"><small>{statusLabel(activePlan.status)} · 当前用户 {user}</small><strong>{activePlan.title}</strong><span>{activePlan.description}</span></span>
              <span className="active-task-progress"><b>{activePlan.progress.percent}%</b><span className="progress-line"><i style={{ width: `${activePlan.progress.percent}%` }} /></span></span>
                <button className="task-inline-btn" onClick={() => setActiveTaskOpen((value: boolean) => !value)}>{activeTaskOpen ? '收起步骤' : '展开步骤'} <ChevronDown size={13} /></button>
              <button className="task-inline-btn primary" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}>任务中枢</button>
            </div>
            <div className="active-task-detail">{activePlan.steps.slice(0, 6).map((step: PlanSummary['steps'][number], index: number) => <div className={`active-task-step ${step.status}`} key={step.step_id}><i>{step.status === 'completed' ? '✓' : index + 1}</i><span><strong>{step.title}</strong><small>{statusLabel(step.status)} · {step.description}</small></span></div>)}</div>
          </article>}
          <div className="quick-start">
            {quickStartCards.map(({ prompt, icon: Icon, title, desc, tone }) => (
              <button key={prompt} className={`quick-card quick-card-${tone}`} onClick={() => setDraft(prompt)}>
                <span className="quick-icon"><Icon size={17} /></span>
                <strong>{title}</strong>
                <span>{desc}</span>
              </button>
            ))}
          </div>
          <RecentActivityCard
            className="welcome-recent-status"
            scheduledTasks={recentTasks}
            senseData={recentSenseData}
            onViewAllTasks={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
            onViewAllSenseData={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
            onTaskClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
            onSenseDataClick={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
          />
        </section>
      )}
      {historyQuery.isLoading && <div className="center-state">正在加载历史…</div>}
      {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">该会话尚无已提交历史，可以直接发送第一条消息。</div>}
      <div className={`messages ${items.length ? 'show' : ''}`}>
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
                <article className="message user" data-user-message-id={item.id}>
                  <UserMessageAvatar avatarUrl={userAvatarUrl} />
                  <div className="message-body">
                    {item.content ? <div className="bubble"><PlainTextMessage content={item.content} /></div> : null}
                    {item.attachments?.length ? (
                      <div className="user-attachment-list">
                        {item.attachments.map((attachment, index) => (
                          <UserAttachmentCard key={attachment.asset_id || `${attachment.name}_${index}`} user={user} attachment={attachment} />
                        ))}
                      </div>
                    ) : null}
                    <div className="message-actions">
                      {item.edited ? <span className="edited-label">编辑后重发</span> : null}
                      {editedSources.has(item.id) ? <span className="edited-label">已用于重发</span> : null}
                      {!running && item.content && lastUserMessage?.id === item.id ? <button onClick={() => void editAndResend(item.id, item.content)} disabled={Boolean(conversationBusy)} aria-label="编辑后重发"><Pencil size={12} />{conversationBusy === 'edit' ? '正在撤销…' : '编辑重发'}</button> : null}
                      <button onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label="复制消息">{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? '已复制' : '复制'}</button>
                    </div>
                  </div>
                </article>
              </Fragment>
            )
          }

          const { assistantMessages, usageItems, planItems, finalizedGuidance } = partitionAssistantTurnItems(block.items)
          const assistantText = assistantMessages.map((item) => item.content).filter(Boolean).join('\n\n')
          const assistantCopyId = assistantMessages.at(-1)?.id || block.id
          const hasPlanBubble = block.items.some((item) => item.kind === 'task_plan')
          return (
            <article key={block.id} className="assistant-turn">
              <div className="msg-avatar assistant-turn-avatar"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent" /></div>
              <div className="assistant-turn-content">
                {block.items.map((item) => {
                  if (item.kind === 'context_compression') return null
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
                  if (item.kind === 'tool') return <ToolCallCard key={item.id} item={item} />
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
                      <button className="assistant-turn-copy" onClick={() => void copyMessage(assistantCopyId, assistantText)} disabled={!assistantText} aria-label="复制智能体回复">
                        {copiedItem === assistantCopyId ? <Check size={13} /> : <Copy size={13} />}{copiedItem === assistantCopyId ? '已复制' : '复制'}
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
    <div className="composer-zone">
      {runRetryNotice ? (
        <div className="run-retry-bubble" role="status" aria-live="polite">
          <span>运行出现问题，正在自动重试（第 {runRetryNotice.nextAttempt}/{runRetryNotice.maxAttempts} 次）</span>
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
      {guidancePreviewItem ? <div className="composer-guidance-preview" aria-live="polite"><GuidanceMessage user={user} item={guidancePreviewItem} placement="current" onCancel={pendingNextTurn?.status === 'error' && liveSessionId ? () => removeNextTurnMessage(user, liveSessionId, pendingNextTurn.id) : undefined} onRetry={pendingNextTurn?.status === 'error' && liveSessionId ? () => setNextTurnMessageStatus(user, liveSessionId, pendingNextTurn.id, 'queued') : undefined} /></div> : null}
      <AgentComposer
        value={draft}
        placeholder={user ? stopping ? '输入下一轮消息；将在当前任务停止后自动发送…' : running ? '输入运行中引导；将在下一个 Provider/工具边界生效…' : '给 kemo-agent 发送消息…' : '请先选择用户'}
        currentRound={currentRound}
        totalRounds={totalRounds}
        roundLimit={roundLimit}
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
            <div className="conversation-menu-head">对话操作</div>
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
              <span className="conversation-action-copy"><strong>手动进行一次上下文压缩</strong><span>{conversationBusy === 'compress' ? '正在压缩并提取记忆…' : '整理当前上下文并同步提取待处理记忆'}</span></span>
            </button>
            <button className="conversation-action long-task-action" role="menuitemcheckbox" aria-checked={Boolean(longTaskQuery.data?.long_task.enabled)} disabled={!sessionId || longTaskBusy || longTaskQuery.isLoading} onClick={() => { void toggleLongTask() }}>
              <span className="conversation-action-icon"><Workflow size={16} /></span>
              <span className="conversation-action-copy"><strong>长任务模式</strong><span>{longTaskQuery.data?.long_task.status === 'running' ? '正在跨 Run 执行；关闭后会在当前 Run 收束时停止续跑' : longTaskQuery.data?.long_task.enabled ? '已允许；达到单轮工具上限后自动续跑' : '允许当前对话达到单轮工具上限后自动续跑'}</span></span>
              <span className={`conversation-switch ${longTaskQuery.data?.long_task.enabled ? 'on' : ''}`} aria-hidden="true"><i /></span>
            </button>
            <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy) || !lastUserMessage} onClick={() => void regenerateLastResponse()}>
              <span className="conversation-action-icon"><RotateCcw size={16} /></span>
              <span className="conversation-action-copy"><strong>重新发送一次消息</strong><span>撤销上一轮后重放原消息，不增加对话轮数</span></span>
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
        onSubmit={() => { if (running) void sendGuidance(); else void send(undefined, { uploadedFiles: pendingUploads }) }}
        onStop={() => { void stopCurrentRun() }}
      />
    </div>
    <KnowledgeReferenceDrawer
      open={knowledgeDrawerOpen}
      documents={knowledgeQuery.data?.documents ?? []}
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
