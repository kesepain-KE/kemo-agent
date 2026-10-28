import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Bot, File, ListChecks, Maximize2, Minimize2, NotebookPen, PanelRight, PanelRightClose, Pin, Terminal, X } from 'lucide-react'
import { t, useLanguage } from '../../i18n'
import { openWorkspaceAgents, openWorkspaceBlank, openWorkspaceJobs, useWorkspaceStore, type WorkspaceTab } from '../../store/workspace'
import { FilePreview } from '../../reference/FilePreview'
import { ShellJobs, ShellJobView } from './ShellJobs'
import { AgentTasks, AgentTaskView } from './AgentTasks'
import { ScratchNote } from './ScratchNote'
import styles from './Workspace.module.css'

function icon(tab: WorkspaceTab) { return tab.type === 'blank' ? <NotebookPen size={14} /> : tab.type === 'agents' || tab.type === 'agent-task' ? <Bot size={14} /> : tab.type === 'file' ? <File size={14} /> : tab.type === 'jobs' ? <ListChecks size={14} /> : <Terminal size={14} /> }

export function WorkspaceLauncher({ user }: { user: string }) {
  useLanguage()
  const current = useWorkspaceStore(state => state.users[user])
  return <button className="icon-btn" aria-label={t('工作面板')} title={t('工作面板')} aria-expanded={current?.open || false} disabled={!user}
    onClick={() => current?.tabs.length ? useWorkspaceStore.getState().patch(user, { open: !current.open }) : openWorkspaceBlank(user)}><PanelRight size={17} /></button>
}

function FilePane({ user, tab, active }: { user: string; tab: Extract<WorkspaceTab, { type: 'file' }>; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!active) ref.current?.querySelectorAll('video,audio').forEach(element => (element as HTMLMediaElement).pause())
  }, [active])
  return <div ref={ref} className={styles.pane}>
    <FilePreview user={user} area={tab.area} file={tab.file} active={active} onClose={() => useWorkspaceStore.getState().close(user, tab.id)} />
  </div>
}

export function WorkspaceLayout({ user, sessionId, children }: { user: string; sessionId: string; children: ReactNode }) {
  useLanguage()
  const workspace = useWorkspaceStore(state => state.users[user])
  const root = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLElement>(null)
  const [size, setSize] = useState(0)
  const [dragging, setDragging] = useState(false)
  const open = Boolean(user && workspace?.open && workspace.tabs.length)
  const overlay = Boolean(open && (size < 1000 || workspace?.maximized))
  const maxWidth = Math.max(360, Math.min(800, size - 540))
  const width = Math.max(360, Math.min(workspace?.width || 480, maxWidth))
  const tabs = [...(workspace?.tabs || [])].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)))

  useLayoutEffect(() => {
    const element = root.current
    if (!element) return
    const update = () => setSize(element.getBoundingClientRect().width)
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!overlay) return
    const previous = document.activeElement as HTMLElement | null
    panel.current?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true })
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }) }
  }, [overlay, user])
  useLayoutEffect(() => {
    if (!open) return
    panel.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
      ?.parentElement?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [open, size, workspace?.activeId, workspace?.tabs])
  const resize = (value: number) => useWorkspaceStore.getState().patch(user, { width: Math.max(360, Math.min(value, maxWidth)) })
  const tabKey = (event: KeyboardEvent, index: number) => {
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    else return
    event.preventDefault()
    useWorkspaceStore.getState().select(user, tabs[next].id)
    panel.current?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus()
  }
  return <div ref={root} className={styles.layout} data-workspace-open={open} data-workspace-overlay={overlay}>
    <div className={styles.main} inert={overlay}>{children}</div>
    {open && !overlay && <div className={styles.resize} role="separator" aria-label={t('调整工作面板宽度')} aria-orientation="vertical"
      aria-valuemin={360} aria-valuemax={maxWidth} aria-valuenow={Math.round(width)} tabIndex={0} style={{ right: width - 4 }}
      onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); setDragging(true) }}
      onPointerMove={event => { if (dragging && root.current) resize(root.current.getBoundingClientRect().right - event.clientX) }}
      onPointerUp={event => { event.currentTarget.releasePointerCapture(event.pointerId); setDragging(false) }}
      onLostPointerCapture={() => setDragging(false)}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing) return
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
          event.preventDefault()
          resize(event.key === 'Home' ? 360 : event.key === 'End' ? maxWidth : width + (event.key === 'ArrowLeft' ? 24 : -24))
        }
      }} />}
    <aside ref={panel} className={`${styles.panel} ${overlay ? styles.overlay : ''}`} style={{ width }} hidden={!open}
      role={overlay ? 'dialog' : 'complementary'} aria-label={t('工作面板')} aria-modal={overlay || undefined}
      onKeyDown={event => {
        if (event.key === 'Escape') { event.stopPropagation(); useWorkspaceStore.getState().patch(user, { open: false }) }
        if (event.key !== 'Tab' || !overlay) return
        const controls = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea,select,[tabindex="0"],a[href]') || [])].filter(element => element.getClientRects().length)
        const first = controls[0], last = controls.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }}>
      <div className={styles.head}>
        <strong>{t('工作面板')}</strong>
        <button className={styles.icon} aria-label={t('草稿本')} title={t('草稿本')} onClick={() => openWorkspaceBlank(user)}><NotebookPen size={16} /></button>
        <button className={styles.icon} aria-label={t('智能体任务')} title={t('智能体任务')} onClick={() => openWorkspaceAgents(user)}><Bot size={16} /></button>
        <button className={styles.icon} aria-label={t('Shell 后台任务')} title={t('Shell 后台任务')} onClick={() => openWorkspaceJobs(user)}><ListChecks size={16} /></button>
        <button className={styles.icon} aria-label={t(workspace?.maximized ? '还原面板' : '最大化面板')} title={t(workspace?.maximized ? '还原面板' : '最大化面板')} onClick={() => useWorkspaceStore.getState().patch(user, { maximized: !workspace?.maximized })}>{workspace?.maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
        <button className={styles.icon} aria-label={t('收起工作面板')} title={t('收起工作面板')} onClick={() => useWorkspaceStore.getState().patch(user, { open: false })}><PanelRightClose size={16} /></button>
      </div>
      <div className={styles.tabs} role="tablist" aria-label={t('工作面板标签')}>
        {tabs.map((tab, index) => <div className={styles.tab} key={tab.id} data-active={tab.id === workspace?.activeId}>
          <button className={styles.tabButton} role="tab" id={`workspace-tab-${index}`} aria-controls={`workspace-pane-${index}`} aria-selected={tab.id === workspace?.activeId}
            tabIndex={tab.id === workspace?.activeId ? 0 : -1} title={tab.title} onClick={() => useWorkspaceStore.getState().select(user, tab.id)} onKeyDown={event => tabKey(event, index)}>
            {icon(tab)}<span>{tab.type === 'blank' || tab.type === 'jobs' || tab.type === 'agents' ? t(tab.title) : tab.title}</span>
          </button>
          <button className={styles.icon} aria-label={`${t(tab.pinned ? '取消固定标签' : '固定标签')} ${tab.title}`} aria-pressed={Boolean(tab.pinned)} onClick={() => useWorkspaceStore.getState().pin(user, tab.id)}><Pin size={12} /></button>
          <button className={styles.icon} aria-label={`${t('关闭标签')} ${tab.title}`} onClick={() => useWorkspaceStore.getState().close(user, tab.id)}><X size={14} /></button>
        </div>)}
      </div>
      {workspace?.notice && <div className={styles.notice} role="status"><span>{t(workspace.notice)}</span><button className={styles.icon} aria-label={t('关闭')} onClick={() => useWorkspaceStore.getState().patch(user, { notice: '' })}><X size={14} /></button></div>}
      <div className={styles.body}>
        {tabs.map((tab, index) => {
          const active = Boolean(open && tab.id === workspace?.activeId)
          return <section className={styles.pane} id={`workspace-pane-${index}`} role="tabpanel" aria-labelledby={`workspace-tab-${index}`} hidden={!active} key={`${user}:${tab.id}`}>
            {tab.type === 'blank' ? <ScratchNote user={user} sessionId={sessionId} />
              : tab.type === 'file' ? <FilePane user={user} tab={tab} active={active} />
                : tab.type === 'jobs' ? <ShellJobs user={user} sessionId={sessionId} active={active} />
                  : tab.type === 'agents' ? <AgentTasks user={user} sessionId={sessionId} active={active} />
                    : tab.type === 'agent-task' ? <AgentTaskView user={user} taskId={tab.taskId} sessionId={tab.sessionId} source={tab.source} active={active} />
                      : <ShellJobView user={user} jobId={tab.jobId} sessionId={tab.sessionId} source={tab.source} active={active} />}
          </section>
        })}
      </div>
    </aside>
  </div>
}
