import type { ReactNode } from 'react'
import { AlertTriangle, BookOpen, BrainCircuit, ListChecks, Settings, Wrench } from 'lucide-react'

type PageKind = 'tasks' | 'knowledge' | 'skills' | 'sense' | 'settings'

const pageCopy: Record<PageKind, { kicker: string; title: string; description: string; icon: ReactNode; cards: string[] }> = {
  tasks: { kicker: 'Task Orchestration', title: '任务中枢', description: '任务计划、Cron 与执行记录的后端接口尚未开放。当前页面只保留信息架构，不展示演示任务。', icon: <ListChecks />, cards: ['任务计划', '定时任务', '执行记录'] },
  knowledge: { kicker: 'File Knowledge', title: '知识库', description: '轻量文件知识检索已存在于 Run，但尚无 Web 浏览、搜索和索引管理接口。', icon: <BookOpen />, cards: ['用户知识', '全局知识', '检索状态'] },
  skills: { kicker: 'Capability Registry', title: '技能中心', description: '技能注册结果尚无只读 Web API。页面不会用静态示例冒充当前已注册技能。', icon: <Wrench />, cards: ['用户技能', '共享技能', '基础插件'] },
  sense: { kicker: 'Context Sources', title: '全局感知', description: '感知来源注册、注入决策与实时状态流尚未实现，此处仅保留未来模块位置。', icon: <BrainCircuit />, cards: ['来源清单', '注入闸门', '决策记录'] },
  settings: { kicker: 'Configuration Overview', title: '配置概览', description: '后端尚未提供脱敏配置镜像与 Provider 状态接口。主题、字号和侧栏偏好可在当前前端使用。', icon: <Settings />, cards: ['外观设置', 'Provider 状态', '运行配置'] },
}

export function PendingModulePage({ kind }: { kind: PageKind }) {
  const page = pageCopy[kind]
  return (
    <div className="view module-view active">
      <div className="module-shell"><div className="module-inner">
        <header className="module-header">
          <div className="module-heading"><div className="module-kicker">{page.kicker}</div><h2>{page.title}</h2><p>{page.description}</p></div>
          <div className="module-actions"><button className="module-btn" disabled>后端待接入</button></div>
        </header>
        <div className="observer-banner"><span className="observer-banner-icon"><AlertTriangle size={17} /></span><span><strong>真实数据优先</strong><small>首期不伪造数量、运行状态、连接状态或操作成功结果。接口开放后再启用对应控件。</small></span><span className="observer-badge">Not connected</span></div>
        <section className="metric-strip pending-metrics">
          {page.cards.map((card, index) => <article className="metric-card" key={card}><div className="metric-top"><span>{card}</span><span className="metric-symbol">0{index + 1}</span></div><strong>待接入</strong></article>)}
        </section>
        <section className="pending-module-panel"><span className="pending-icon">{page.icon}</span><h3>{page.title}界面骨架已迁移</h3><p>这里保留原型的层级、卡片和响应式语言，但不会让未接后端的按钮产生假操作。</p><button disabled className="module-btn primary">接口尚未开放</button></section>
      </div></div>
    </div>
  )
}
