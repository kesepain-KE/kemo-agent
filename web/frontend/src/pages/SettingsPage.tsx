import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Database, RefreshCw, Wrench } from 'lucide-react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { getMemorySummary, getPromptDiagnostics, getSettings, getUserConfig, updateUserConfig } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import { useUiStore } from '../store/ui'

type SettingsTab = 'appearance' | 'provider' | 'users' | 'memory' | 'permissions' | 'runtime' | 'prompt' | 'config'

const settingsTabs = new Set<SettingsTab>(['appearance', 'provider', 'users', 'memory', 'permissions', 'runtime', 'prompt', 'config'])

function isSettingsTab(value: string | null): value is SettingsTab {
  return value !== null && settingsTabs.has(value as SettingsTab)
}

const credentialLabels: Record<string, string> = {
  environment: '环境变量已配置', inline: '配置文件内联', missing: '凭据未检测到',
}

function sourceModeLabel(policy?: { mode: 'all' | 'allowlist'; names: string[] }) {
  if (!policy) return '—'
  return policy.mode === 'all' ? '全量' : policy.names.join('、')
}

const sourceLabels: Record<string, string> = { user: '用户', global: '全局', default: '默认' }

function SettingRow({ title, description, control, source }: { title: string; description: string; control: React.ReactNode; source?: string }) {
  return <div className="setting-row"><span className="setting-copy"><strong>{title}{source ? <i className={`config-source ${source}`}>{sourceLabels[source] || source}</i> : null}</strong><span>{description}</span></span><span className="setting-control">{control}</span></div>
}

export function SettingsPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const [searchParams] = useSearchParams()
  const ui = useUiStore()
  const queryClient = useQueryClient()
  const requestedTab = searchParams.get('tab')
  const [tab, setTab] = useState<SettingsTab>(() => isSettingsTab(requestedTab) ? requestedTab : 'appearance')
  const query = useQuery({ queryKey: ['settings', user], queryFn: () => getSettings(user), enabled: Boolean(user) })
  const configQuery = useQuery({ queryKey: ['user-config', user], queryFn: () => getUserConfig(user), enabled: Boolean(user) })
  const promptQuery = useQuery({ queryKey: ['prompt-diagnostics', user], queryFn: () => getPromptDiagnostics(user), enabled: Boolean(user && tab === 'prompt') })
  const memoryQuery = useQuery({ queryKey: ['memory-summary', user], queryFn: () => getMemorySummary(user), enabled: Boolean(user && tab === 'memory') })
  const [configDraft, setConfigDraft] = useState('')
  const [configSaving, setConfigSaving] = useState(false)
  const [configNotice, setConfigNotice] = useState('')
  const [configError, setConfigError] = useState('')
  const data = query.data

  useEffect(() => {
    if (configQuery.data) setConfigDraft(JSON.stringify(configQuery.data.config, null, 2))
  }, [configQuery.data])

  useEffect(() => {
    if (isSettingsTab(requestedTab)) setTab(requestedTab)
  }, [requestedTab])

  const saveConfig = async () => {
    if (!configQuery.data?.write_enabled || configSaving) return
    setConfigNotice('')
    setConfigError('')
    let parsed: Record<string, unknown>
    try {
      const value = JSON.parse(configDraft) as unknown
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('根节点必须是对象')
      parsed = value as Record<string, unknown>
    } catch (error) {
      setConfigError(`JSON 无效：${error instanceof Error ? error.message : '无法解析'}`)
      return
    }
    setConfigSaving(true)
    try {
      const saved = await updateUserConfig(user, parsed, configQuery.data.etag)
      queryClient.setQueryData(['user-config', user], saved)
      setConfigDraft(JSON.stringify(saved.config, null, 2))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['settings', user] }),
        queryClient.invalidateQueries({ queryKey: ['knowledge', user] }),
        queryClient.invalidateQueries({ queryKey: ['skills', user] }),
        queryClient.invalidateQueries({ queryKey: ['sense', user] }),
        queryClient.invalidateQueries({ queryKey: ['overview', user] }),
      ])
      setConfigNotice('保存成功；下一次请求将读取新配置。')
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : '配置保存失败')
    } finally {
      setConfigSaving(false)
    }
  }

  return (
    <ModuleFrame
      kicker="Configuration Overview"
      title="配置概览"
      description="查看脱敏后的运行配置镜像；仅主题与字号在 Web 中可修改，其余设置仍由配置文件和专用流程维护。"
      actions={<button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />重新读取</button>}
    >
      {query.isError && <ModuleError />}
      <div className="observer-banner settings-observer-banner">
        <span className="observer-banner-icon">R</span>
        <span><strong>默认只读</strong><small>接口不会返回 API Key、环境变量值、完整配置对象或内部路径。</small></span>
        <span className="observer-badge">Schema v{data?.schema_version || '—'}</span>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="配置分类">
          {([
            ['appearance', '外观与主题'], ['provider', '模型与 Provider'], ['users', '用户切换'],
            ['memory', '记忆与上下文'], ['permissions', '权限边界'], ['runtime', '运行限制'],
            ['prompt', 'Prompt 与 Expand'],
            ['config', '用户配置 JSON'],
          ] as const).map(([value, label]) => <button key={value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}><span>{label}</span><span>›</span></button>)}
        </nav>

        <div className="settings-content">
          {tab === 'appearance' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>界面主题</strong><span>侧边栏、工作区、浮层与控件使用统一语义色。</span></div>
              <div className="setting-row theme-setting-row">
                <span className="setting-copy"><strong>明暗主题</strong><span>高级白与高级黑共享相同的层级、边界和交互语义。</span></span>
                <div className="theme-choice-group" role="radiogroup" aria-label="界面主题">
                  <button className={`theme-choice ${ui.theme === 'light' ? 'active' : ''}`} role="radio" aria-checked={ui.theme === 'light'} onClick={() => ui.setTheme('light')}><span className="theme-preview light"><span className="tp-side" /><span className="tp-top" /><span className="tp-card" /><span className="tp-line" /></span><span className="theme-choice-copy"><span><strong>高级白</strong><span>统一灰白 · 低对比边界</span></span><i className="theme-choice-check">✓</i></span></button>
                  <button className={`theme-choice ${ui.theme === 'dark' ? 'active' : ''}`} role="radio" aria-checked={ui.theme === 'dark'} onClick={() => ui.setTheme('dark')}><span className="theme-preview dark"><span className="tp-side" /><span className="tp-top" /><span className="tp-card" /><span className="tp-line" /></span><span className="theme-choice-copy"><span><strong>高级黑</strong><span>中性黑灰 · 层级一致</span></span><i className="theme-choice-check">✓</i></span></button>
                </div>
              </div>
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>界面字号</strong><span>调整文字比例，并同步适配顶部栏组件与间距。</span></div>
              <div className="setting-row font-setting-row">
                <span className="setting-copy"><strong>全局界面比例</strong><span>小、中、大三级同步缩放文字与关键控件，默认使用“中”。</span></span>
                <div className="font-choice-group" role="radiogroup" aria-label="界面字号">{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={ui.fontSize === size ? 'active' : ''} role="radio" aria-checked={ui.fontSize === size} onClick={() => ui.setFontSize(size)}><b>{size === 'small' ? '小' : size === 'medium' ? '中' : '大'}</b><span>{size === 'small' ? '72%' : size === 'medium' ? '88%' : '105%'}</span></button>)}</div>
              </div>
            </article>
          </>}

          {tab === 'provider' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>Provider 与基础 Chat API</strong><span>只展示路由元数据，不探测或调用模型。</span></div>
              <SettingRow title="Provider 类型" description="当前合并配置中的适配器" source={data?.provenance['provider.type']} control={<span className="select-like">{data?.provider.type || '—'}</span>} />
              <SettingRow title="模型" description="当前默认模型标识" source={data?.provenance['provider.model']} control={<span className="select-like wide">{data?.provider.model || '—'}</span>} />
              <SettingRow title="兼容端点" description="仅显示配置的基础 URL" source={data?.provenance['provider.base_url']} control={<span className="select-like wide">{data?.provider.base_url || '—'}</span>} />
              <SettingRow title="凭据状态" description="只返回来源状态，不返回变量名或值" control={<StatusChip status={data?.provider.credential_source === 'missing' ? 'missing' : 'configured'}>{credentialLabels[data?.provider.credential_source || 'missing']}</StatusChip>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>调用参数</strong><span>Provider 运行时的只读镜像。</span></div>
              <SettingRow title="请求超时" description="单次 Provider 调用上限" source={data?.provenance['provider.timeout']} control={<span className="value-pill">{data?.provider.timeout ?? '—'} 秒</span>} />
              <SettingRow title="原生流式" description="Provider 配置中的 stream 开关；Web 自身仍使用 SSE" source={data?.provenance['provider.stream']} control={<StatusChip status={data?.provider.stream ? 'enabled' : 'paused'}>{data?.provider.stream ? '已开启' : '已关闭'}</StatusChip>} />
            </article>
          </>}

          {tab === 'users' && <>
            <article className="setting-section current-user-section">
              <div className="setting-section-head"><strong>用户切换机制</strong><span>每个 Web 窗口只激活一个 users/&lt;user_id&gt; 目录，不并行运行多个智能体。</span></div>
              <div className="current-user-card"><span className="user-card-avatar">{user.slice(0, 1).toUpperCase()}</span><span><strong>{user}</strong><small>users/{user} · 当前用户</small></span><StatusChip status="enabled">已载入</StatusChip></div>
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>可用用户</strong><span>切换操作位于左侧栏底部。</span></div>
              <div className="user-space-list">{data?.users.map((name) => <div className="user-space-row" key={name}><span className="mini-avatar">{name.slice(0, 1).toUpperCase()}</span><span><strong>{name}</strong><small>users/{name}</small></span><StatusChip status={name === user ? 'enabled' : 'gray'}>{name === user ? '当前' : '可切换'}</StatusChip></div>)}</div>
            </article>
          </>}

          {tab === 'memory' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>上下文窗口</strong><span>历史窗口的真实 Token 使用量会显示在顶栏。</span></div>
              <SettingRow title="Token 上限" description="达到限制前由上下文生命周期执行压缩" source={data?.provenance['agents.n4_token_limit']} control={<span className="value-pill">{data?.limits.context_tokens.toLocaleString() || '—'}</span>} />
              <SettingRow title="压缩比例" description="上下文压缩后的目标比例" source={data?.provenance['agents.n5_token_compression_ratio']} control={<span className="value-pill">{data ? Math.round(data.limits.compression_ratio * 100) : '—'}%</span>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>记忆管线</strong><span>抽取与注入开关来自当前用户合并配置。</span></div>
              <SettingRow title="记忆抽取" description="成功对话后生成候选记忆" source={data?.provenance['memory.extraction_enabled']} control={<StatusChip status={data?.features.memory_extraction ? 'enabled' : 'paused'} />} />
              <SettingRow title="记忆注入" description={`临时层最多 ${data?.limits.memory_items || '—'} 条；重要记忆 ${data?.limits.memory_chars || '—'} 字符；永久记忆全部注入`} source={data?.provenance['memory.injection_enabled']} control={<StatusChip status={data?.features.memory_injection ? 'enabled' : 'paused'} />} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>记忆库存</strong><span>只读预览；显示文件名、挡位权重和固定到期时间。</span></div>
              <div className="memory-tier-strip">{(['seven_days', 'one_month', 'half_year', 'permanent'] as const).map((tier) => <span key={tier}><small>{tier}</small><strong>{memoryQuery.data?.summary[tier] ?? '—'}</strong></span>)}</div>
              <div className="memory-observer-list">{memoryQuery.data?.items.map((item) => <div className="memory-observer-row" key={item.filename}><span><strong>{item.preview || item.filename}</strong><small>{item.tier} · {item.filename}</small></span><span><b>{item.tier === 'permanent' ? '永久' : `weight ${item.weight}`}</b><small>{item.expires_at ? `到期 ${item.expires_at}` : '全部注入'}</small></span></div>)}{memoryQuery.isSuccess && !memoryQuery.data.items.length ? <span className="drawer-empty">当前用户没有记忆条目。</span> : null}</div>
            </article>
          </>}

          {tab === 'permissions' && <article className="setting-section">
            <div className="setting-section-head"><strong>安全与数据边界</strong><span>Web Observer API 的固定策略。</span></div>
            <SettingRow title="Web 访问控制" description="Token 或账号密码任一模式启用时，业务 API 需要签名会话" control={<StatusChip status={data?.authentication.enabled ? 'enabled' : 'gray'}>{data?.authentication.enabled ? '已启用' : '兼容开放'}</StatusChip>} />
            <SettingRow title="Token 登录" description="仅显示认证模式状态，不返回访问令牌" control={<StatusChip status={data?.authentication.token_enabled ? 'enabled' : 'gray'}>{data?.authentication.token_enabled ? '可用' : '未配置'}</StatusChip>} />
            <SettingRow title="账号密码登录" description="用户名和密码不会进入设置响应" control={<StatusChip status={data?.authentication.password_enabled ? 'enabled' : 'gray'}>{data?.authentication.password_enabled ? '可用' : '未配置'}</StatusChip>} />
            <SettingRow title="Session Cookie" description="浏览器会话使用 HttpOnly 签名 Cookie" control={<StatusChip status={data?.authentication.session_cookie_configured ? 'enabled' : 'gray'}>{data?.authentication.session_cookie_configured ? '已配置' : '未启用'}</StatusChip>} />
            <SettingRow title="跨用户资源访问" description="路径参数必须对应已存在用户，历史按 user/source/session 隔离" control={<StatusChip status="enabled">已隔离</StatusChip>} />
            <SettingRow title="敏感配置返回" description="API Key、环境变量值与完整配置不会进入响应" control={<StatusChip status="enabled">已脱敏</StatusChip>} />
            <SettingRow title="系统级修改" description="任务、配置、技能和来源页面默认只读" control={<StatusChip status="paused">Web 禁止</StatusChip>} />
          </article>}

          {tab === 'runtime' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>功能开关</strong><span>当前用户合并配置中的运行能力。</span></div>
              <SettingRow title="工具调用" description="Run 工具编排" source={data?.provenance['tools.enabled']} control={<StatusChip status={data?.features.tools ? 'enabled' : 'paused'}><Wrench size={12} />{data?.features.tools ? '已启用' : '已停用'}</StatusChip>} />
              <SettingRow title="文件知识" description="本地文件索引与 Prompt 注入" source={data?.provenance['knowledge.enabled']} control={<StatusChip status={data?.features.knowledge ? 'enabled' : 'paused'}><Database size={12} />{data?.features.knowledge ? '已启用' : '已停用'}</StatusChip>} />
              <SettingRow title="任务计划自动接受" description="计划是否跳过人工批准" source={data?.provenance['task_plan.auto_accept']} control={<StatusChip status={data?.features.task_plan_auto_accept ? 'enabled' : 'paused'}><Bot size={12} />{data?.features.task_plan_auto_accept ? '已开启' : '需确认'}</StatusChip>} />
              <SettingRow title="Cron 调度" description="Web 不启动调度器，只显示主宿主配置" source={data?.provenance['cron.enabled']} control={<StatusChip status={data?.features.cron ? 'enabled' : 'paused'}>{data?.features.cron ? '已启用' : '已停用'}</StatusChip>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>主智能体来源策略</strong><span>注册阶段保留完整库存，以下策略只在 Prompt 选择与知识检索阶段过滤。</span></div>
              <SettingRow title="知识范围" description="enabled=false 时索引注入与正文搜索都为空" control={<span className="value-pill">{data?.source_policy.knowledge.effective_scopes.join(' / ') || '无'}</span>} />
              <SettingRow title="共享 / 用户技能" description="空白名单表示全量启用" control={<span className="value-pill">{sourceModeLabel(data?.source_policy.skills.shared)} / {sourceModeLabel(data?.source_policy.skills.user)}</span>} />
              <SettingRow title="全局 / 共享 Expand" description="用户 Expand 始终按当前用户目录动态注册" control={<span className="value-pill">{sourceModeLabel(data?.source_policy.expand.global)} / {sourceModeLabel(data?.source_policy.expand.shared)}</span>} />
              <SettingRow title="全局感知模块" description="按 global_sense 直接子目录名过滤" control={<span className="value-pill">{sourceModeLabel(data?.source_policy.perception.global)}</span>} />
              <SettingRow title="kemo-graph" description="独立项目连接占位；本服务不会启动或调用其 CLI" control={<StatusChip status={data?.source_policy.kemo_graph.status || 'disabled'}>{data?.source_policy.kemo_graph.status === 'not_connected' ? '已请求 / 未连接' : '未启用'}</StatusChip>} />
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>执行限制</strong><span>用于约束工具、知识和任务计划。</span></div>
              <SettingRow title="工具迭代" description={`单次调用超时 ${data?.limits.tool_timeout || '—'} 秒`} source={data?.provenance['tools.max_iterations']} control={<span className="value-pill">{data?.limits.tool_iterations || '—'} 轮</span>} />
              <SettingRow title="任务步骤" description="单个计划最大步骤数" source={data?.provenance['task_plan.max_steps']} control={<span className="value-pill">{data?.limits.task_plan_steps || '—'} 步</span>} />
              <SettingRow title="知识注入" description={`最多 ${data?.limits.knowledge_chars || '—'} 字符`} source={data?.provenance['knowledge.max_items']} control={<span className="value-pill">{data?.limits.knowledge_items || '—'} 项</span>} />
            </article>
          </>}

          {tab === 'prompt' && <>
            <article className="setting-section">
              <div className="setting-section-head"><strong>Prompt 注入诊断</strong><span>不返回正文，只展示固定 14 段的体积、条目、截断和来源文件。</span></div>
              <div className="prompt-total"><span>当前系统 Prompt</span><strong>{promptQuery.data?.total_chars.toLocaleString() ?? '—'} 字符</strong></div>
              <div className="prompt-section-list">{promptQuery.data?.sections.map((section) => {
                const percent = section.original_chars ? Math.min(100, Math.round(section.injected_chars * 100 / section.original_chars)) : 0
                return <div className="prompt-section-row" key={section.name}><span><strong>{section.name}</strong><small>{section.injected_items}/{section.original_items} 项 · {section.source_files.join('、') || '无文件来源'}</small></span><span className="prompt-section-meter"><i style={{ width: `${percent}%` }} /></span><span><b>{section.injected_chars}/{section.original_chars}</b><StatusChip status={section.truncated ? 'warning' : section.status === 'injected' ? 'enabled' : 'gray'}>{section.truncated ? '已截断' : section.status === 'injected' ? '已注入' : '省略'}</StatusChip></span></div>
              })}</div>
            </article>
            <article className="setting-section">
              <div className="setting-section-head"><strong>Expand 注册与过滤</strong><span>注册库存与当前用户主智能体选择结果分开展示。</span></div>
              <div className="expand-observer-grid">{Object.entries(promptQuery.data?.expand || {}).map(([scope, item]) => <div key={scope}><strong>{scope}</strong><span>发现 {item.discovered.length} · 选择 {item.selected.length}</span><small>已过滤：{item.filtered.join('、') || '无'}</small><small>未匹配：{item.unmatched.join('、') || '无'}</small></div>)}</div>
            </article>
          </>}

          {tab === 'config' && <>
            <article className="setting-section config-editor-section">
              <div className="setting-section-head"><strong>users/{user}/user_config.json</strong><span>保存后仅影响下一次请求；当前正在执行的任务继续使用启动时的配置快照。</span></div>
              <div className={`config-write-banner ${configQuery.data?.write_enabled ? 'enabled' : 'locked'}`}>
                <span><strong>{configQuery.data?.write_enabled ? '已启用安全写入' : '只读模式'}</strong><small>{configQuery.data?.write_enabled ? '使用 ETag 防冲突和原子替换，敏感字段保持原值。' : '需要启用 Web 认证并设置 WEB_ALLOW_CONFIG_WRITE=true。'}</small></span>
                <StatusChip status={configQuery.data?.write_enabled ? 'enabled' : 'paused'}>{configQuery.data?.write_enabled ? '可保存' : '不可写'}</StatusChip>
              </div>
              {configQuery.isError ? <div className="config-editor-error">用户配置读取失败。</div> : null}
              <textarea className="config-json-editor" value={configDraft} onChange={(event) => setConfigDraft(event.target.value)} spellCheck={false} aria-label="用户配置 JSON" />
              <div className="config-editor-foot">
                <span>脱敏字段：{configQuery.data?.redacted_paths.join('、') || '无'}；`***` 保存时不会覆盖磁盘密钥。</span>
                <div><button className="module-btn" onClick={() => configQuery.data && setConfigDraft(JSON.stringify(configQuery.data.config, null, 2))}>撤销编辑</button><button className="module-btn primary" disabled={!configQuery.data?.write_enabled || configSaving} onClick={() => void saveConfig()}>{configSaving ? '正在保存…' : '校验并保存'}</button></div>
              </div>
              {configNotice ? <div className="config-editor-notice">{configNotice}</div> : null}
              {configError ? <div className="config-editor-error">{configError}</div> : null}
            </article>
          </>}
        </div>
      </div>
    </ModuleFrame>
  )
}
