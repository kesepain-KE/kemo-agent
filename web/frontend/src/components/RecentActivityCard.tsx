import type { ReactNode } from 'react'
import {
  AlarmClock,
  CalendarDays,
  ChevronRight,
  ClipboardList,
  CloudSun,
  Droplets,
  MapPin,
  Radio,
  Thermometer,
} from 'lucide-react'
import styles from './RecentActivityCard.module.css'

export type ScheduledTaskIcon = 'clipboard' | 'location' | 'calendar' | 'alarm'
export type ScheduledTaskStatus = 'enabled' | 'running' | 'completed' | 'paused' | 'failed' | 'cancelled' | 'disabled'
export type SenseDataIcon = 'temperature' | 'humidity' | 'weather' | 'radio'

export interface ScheduledTaskItem {
  id: string
  title: string
  schedule: string
  nextRun: string
  status: ScheduledTaskStatus
  icon?: ScheduledTaskIcon
}

export interface SenseDataItem {
  id: string
  name: string
  value: string
  updateInterval: string
  updatedAt: string
  injected: boolean
  icon?: SenseDataIcon
}

export interface RecentActivityCardProps {
  scheduledTasks: ScheduledTaskItem[]
  senseData: SenseDataItem[]
  title?: string
  className?: string
  maxTaskItems?: number
  maxSenseItems?: number
  onViewAllTasks?: () => void
  onViewAllSenseData?: () => void
  onTaskClick?: (task: ScheduledTaskItem) => void
  onSenseDataClick?: (item: SenseDataItem) => void
}

const taskIconMap: Record<ScheduledTaskIcon, ReactNode> = {
  clipboard: <ClipboardList size={19} strokeWidth={1.9} />,
  location: <MapPin size={19} strokeWidth={1.9} />,
  calendar: <CalendarDays size={19} strokeWidth={1.9} />,
  alarm: <AlarmClock size={19} strokeWidth={1.9} />,
}

const senseIconMap: Record<SenseDataIcon, ReactNode> = {
  temperature: <Thermometer size={19} strokeWidth={1.9} />,
  humidity: <Droplets size={19} strokeWidth={1.9} />,
  weather: <CloudSun size={19} strokeWidth={1.9} />,
  radio: <Radio size={19} strokeWidth={1.9} />,
}

const taskStatusMeta: Record<ScheduledTaskStatus, { label: string; className: string }> = {
  enabled: { label: '已启用', className: styles.statusEnabled },
  running: { label: '运行中', className: styles.statusRunning },
  completed: { label: '已完成', className: styles.statusCompleted },
  paused: { label: '已暂停', className: styles.statusDisabled },
  failed: { label: '执行失败', className: styles.statusFailed },
  cancelled: { label: '已取消', className: styles.statusDisabled },
  disabled: { label: '已停用', className: styles.statusDisabled },
}

function cx(...classNames: Array<string | undefined | false>) {
  return classNames.filter(Boolean).join(' ')
}

function SectionHeader({ icon, title, onViewAll }: { icon: ReactNode; title: string; onViewAll?: () => void }) {
  return <div className={styles.sectionHeader}>
    <div className={styles.sectionTitle}><span className={styles.sectionTitleIcon}>{icon}</span><span>{title}</span></div>
    {onViewAll && <button type="button" className={styles.viewAllButton} onClick={onViewAll}><span>查看全部</span><ChevronRight size={15} strokeWidth={1.8} /></button>}
  </div>
}

function EmptyState({ text }: { text: string }) {
  return <div className={styles.emptyState}>{text}</div>
}

export function RecentActivityCard({
  scheduledTasks,
  senseData,
  title = '最近状态',
  className,
  maxTaskItems = 4,
  maxSenseItems = 3,
  onViewAllTasks,
  onViewAllSenseData,
  onTaskClick,
  onSenseDataClick,
}: RecentActivityCardProps) {
  const visibleTasks = scheduledTasks.slice(0, maxTaskItems)
  const visibleSenseData = senseData.slice(0, maxSenseItems)
  return <section className={cx(styles.card, className)} aria-labelledby="recent-activity-title">
    <h2 id="recent-activity-title" className={styles.cardTitle}>{title}</h2>
    <div className={styles.scrollArea}>
    <div className={styles.section}>
      <SectionHeader icon={<AlarmClock size={21} strokeWidth={1.9} />} title="定时任务" onViewAll={onViewAllTasks} />
      <div className={styles.list}>
        {visibleTasks.length === 0 ? <EmptyState text="当前没有已配置的用户定时任务" /> : visibleTasks.map((task, index) => {
          const status = taskStatusMeta[task.status]
          return <button key={task.id} type="button" className={styles.taskRow} onClick={() => onTaskClick?.(task)}>
          <span className={cx(styles.itemIcon, styles[`taskIcon${(index % 4) + 1}`])}>{taskIconMap[task.icon ?? 'clipboard']}</span>
          <span className={styles.taskMain}><strong className={styles.itemName}>{task.title}</strong><span className={styles.itemDescription}>{task.schedule}</span></span>
          <span className={styles.taskNextRun}><span className={styles.metadataLabel}>下次运行：</span><span className={styles.metadataValue}>{task.nextRun}</span></span>
          <span className={cx(styles.statusPill, status.className)}>{status.label}</span>
        </button>})}
      </div>
    </div>
    <div className={styles.section}>
      <SectionHeader icon={<Radio size={21} strokeWidth={1.9} />} title="感知数据注入" onViewAll={onViewAllSenseData} />
      <div className={styles.list}>
        {visibleSenseData.length === 0 ? <EmptyState text="当前没有正在注入的感知数据" /> : visibleSenseData.map((item, index) => <button key={item.id} type="button" className={styles.senseRow} onClick={() => onSenseDataClick?.(item)}>
          <span className={cx(styles.itemIcon, styles[`senseIcon${(index % 3) + 1}`])}>{senseIconMap[item.icon ?? 'radio']}</span>
          <strong className={styles.itemName}>{item.name}</strong>
          <span className={styles.intervalPill}>{item.updateInterval || '频率未声明'}</span>
          <span className={styles.senseValue}><span className={styles.metadataLabel}>最新值：</span><span className={styles.metadataValue}>{item.value || '暂无数据'}</span></span>
          <span className={styles.senseUpdateTime}><span className={styles.metadataLabel}>更新时间：</span><span className={styles.metadataValue}>{item.updatedAt}</span></span>
          <span className={cx(styles.statusPill, item.injected ? styles.statusEnabled : styles.statusDisabled)}>{item.injected ? '已注入' : '未注入'}</span>
        </button>)}
      </div>
    </div>
    </div>
  </section>
}
