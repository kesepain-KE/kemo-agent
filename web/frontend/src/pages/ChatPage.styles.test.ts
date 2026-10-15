import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles/app.css'), 'utf8')
const production = readFileSync(resolve(process.cwd(), 'src/reference/styles/production.css'), 'utf8')
const chatView = readFileSync(resolve(process.cwd(), 'src/pages/ChatPageView.tsx'), 'utf8')
const welcomeStyles = readFileSync(resolve(process.cwd(), 'src/reference/styles/chat.css'), 'utf8')

describe('欢迎页左右卡片', () => {
  it('左右等宽、等高，标题等高且不额外偏移统计卡片', () => {
    expect(welcomeStyles).toContain('grid-template-columns: repeat(2, minmax(0, 1fr))')
    const header = welcomeStyles.match(/\.welcome-section-head\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(header).toContain('min-height: 26px')
    for (const selector of ['welcome-metric', 'recent-task']) {
      const card = welcomeStyles.match(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
      expect(card).toContain('height: 59px')
    }
    const panel = welcomeStyles.match(/\.welcome-metrics-panel\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(panel).not.toContain('margin-top')
  })

  it('数字位于卡片右侧并采用轻字重与次级灰度', () => {
    const value = welcomeStyles.match(/\.welcome-metric strong\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(value).toContain('grid-column: 2')
    expect(value).toContain('grid-row: 1 / span 2')
    expect(value).toContain('text-align: right')
    expect(value).toContain('font-weight: 450')
    expect(value).toContain('color: var(--text-secondary)')
  })
})

describe('消息两侧头像与输入框对齐', () => {
  it('头像放在消息行内，不再位于正文上方的姓名头部', () => {
    expect(chatView).toContain('<UserMessageAvatar avatarUrl={userAvatarUrl} user={user} />')
    expect(chatView).toContain('<span className="msg-avatar is-assistant" title="kemo-agent"><KemoLogo /></span>')
    expect(chatView).not.toContain('<div className="msg-head">')
  })

  it('头像缩小四分之一并放在公共内容列两侧，操作按钮仍在正文下方', () => {
    const avatar = production.match(/\.chat-stream \.msg > \.msg-avatar\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(production).toContain('--chat-avatar-size: 33px')
    expect(avatar).toContain('width: var(--chat-avatar-size)')
    expect(avatar).toContain('height: var(--chat-avatar-size)')
    expect(avatar).toContain('align-self: start')
    expect(production).toContain('grid-template-columns: var(--chat-avatar-size) minmax(0, 1fr) var(--chat-avatar-size)')
    expect(production).toContain('.msg.is-assistant > .msg-avatar { grid-column: 1; }')
    expect(production).toContain('.msg.is-user > .msg-avatar { grid-column: 3; }')
    expect(production).toContain('.msg.is-assistant > .msg-body > :first-child { margin-top: 0; }')
    const actions = production.match(/\.msg\.is-user > \.user-message-actions\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(actions).toContain('grid-column: 2')
    expect(actions).toContain('grid-row: 2')
  })
})

describe('用户消息独立操作气泡', () => {
  it('操作行位于消息正文容器之外，不再嵌入用户气泡', () => {
    expect(chatView).toMatch(/<\/div>\s*<div className="user-message-actions">/)
    expect(chatView).not.toContain('<div className="msg-head-actions">')
  })

  it('按钮分别使用当前主题背景、边框和圆角，不复用悬停隐藏的头部操作行', () => {
    const actions = production.match(/\.msg\.is-user > \.user-message-actions\s*\{([^}]*)\}/)?.[1] ?? ''
    const button = production.match(/\.user-message-actions > button\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(actions).toContain('justify-content: flex-end')
    expect(actions).toContain('flex-wrap: wrap')
    expect(actions).not.toContain('opacity: 0')
    expect(button).toContain('background: var(--bg-surface)')
    expect(button).toContain('border: 1px solid var(--border-subtle)')
    expect(button).toContain('border-radius: var(--r-full)')
  })
})

describe('智能体正文气泡和横向用量面板', () => {
  it('只给正文添加当前主题气泡，不包住思考过程、工具或底部统计', () => {
    const bubble = production.match(/\.assistant-response > \.bubble\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(bubble).toContain('width: 100%')
    expect(bubble).not.toContain('width: fit-content')
    expect(bubble).toContain('max-width: 100%')
    expect(bubble).toContain('background: var(--bg-surface)')
    expect(bubble).toContain('border: 1px solid var(--border-subtle)')
    expect(bubble).toContain('border-radius: var(--r-lg);')
    expect(bubble).toContain('overflow-wrap: anywhere')
  })

  it('统计横向排列为独立小气泡，窄屏可换行且数值标签不拆开', () => {
    const row = production.match(/\.usage-card-head\s*\{([^}]*)\}/)?.[1] ?? ''
    const chip = production.match(/\.usage-summary-chip\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(row).toContain('display: flex')
    expect(row).toContain('flex-wrap: wrap')
    expect(chip).toContain('white-space: nowrap')
    expect(chip).toContain('background: var(--bg-inset)')
    expect(chip).toContain('border-radius: var(--r-full)')
  })
})

describe('用户正文气泡', () => {
  it('与智能体正文使用相同的主题表面和四角圆弧', () => {
    const userBody = production.match(/\.msg\.is-user > \.msg-body\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(userBody).toContain('background: var(--bg-surface)')
    expect(userBody).toContain('border: 1px solid var(--border-subtle)')
    expect(userBody).toContain('border-radius: var(--r-lg)')
    expect(userBody).not.toContain('border-bottom-right-radius')
  })
})

describe('输入框轮数气泡', () => {
  it('三个轮数共用独立的主题圆角气泡', () => {
    const bubble = production.match(/\.reference-round-bubble\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(bubble).toContain('display: inline-flex')
    expect(bubble).toContain('border-radius: var(--r-full)')
    expect(bubble).toContain('border: 1px solid var(--border-subtle)')
    expect(bubble).toContain('background: var(--bg-surface)')
    expect(bubble).toContain('white-space: nowrap')
  })
})

describe('ChatPage guidance styles', () => {
  it('contains long guidance text inside the conversation width', () => {
    const rules = [...stylesheet.matchAll(/\.guidance-message\s*\{([^}]*)\}/g)]
    const rule = rules.map((match) => match[1]).find((value) => value.includes('min-width: 0')) ?? ''

    expect(rule).toContain('min-width: 0')
    expect(rule).toContain('max-width: 100%')
    expect(rule).toContain('overflow-x: hidden')
    expect(rule).toContain('overflow-y: auto')
  })

  it('scrolls unbroken guidance content horizontally inside its own bubble', () => {
    const rule = stylesheet.match(/\.guidance-message > strong\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(rule).toContain('min-width: 0')
    expect(rule).toContain('max-width: 100%')
    expect(rule).toContain('overflow-x: auto')
    expect(rule).toContain('white-space: pre')
    expect(rule).toContain('scrollbar-width: thin')
  })

  it('limits every guidance bubble to one third of the viewport', () => {
    const rule = stylesheet.match(/\.guidance-message\s*\{([^}]*)\}/g)?.map((value) => value.replace(/^\.guidance-message\s*\{/, '').replace(/\}$/, '')).find((value) => value.includes('max-height: 33.333vh')) ?? ''

    expect(rule).toContain('max-width: 100%')
    expect(rule).toContain('box-sizing: border-box')
    expect(rule).toContain('max-height: 33.333vh')
    expect(rule).toContain('max-height: 33.333dvh')
    expect(rule).toContain('overflow-y: auto')
  })

  it('limits only the composer guidance preview to one auxiliary card unit', () => {
    const previewRule = stylesheet.match(/\.composer-guidance-preview\s*\{([^}]*)\}/)?.[1] ?? ''
    const cardRule = stylesheet.match(/\.composer-guidance-preview > \.guidance-message\.guidance-current\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(previewRule).toContain('min-height: 0')
    expect(previewRule).not.toContain('overflow-y')
    expect(cardRule).toContain('max-height: var(--composer-guidance-max, 10.5rem)')
    expect(cardRule).toContain('overflow-y: auto')
    expect(cardRule).toContain('overscroll-behavior: contain')
  })
})

describe('ChatPage tool-call theme styles', () => {
  it('uses theme surfaces throughout the expanded tool card', () => {
    const bodyRule = stylesheet.match(/\.tool-call-body\s*\{([^}]*)\}/g)?.at(-1) ?? ''
    const panelRule = stylesheet.match(/\.tool-call-panel pre\s*\{([^}]*)\}/g)?.at(-1) ?? ''
    const labelRule = stylesheet.match(/\.tool-call-panel label\s*\{([^}]*)\}/g)?.at(-1) ?? ''

    expect(bodyRule).toContain('background: var(--surface)')
    expect(bodyRule).toContain('border-top-color: var(--line)')
    expect(bodyRule).not.toMatch(/#fff|white/i)
    expect(panelRule).toContain('background: var(--surface-2)')
    expect(panelRule).toContain('color: var(--text-2)')
    expect(panelRule).toContain('border-color: var(--line)')
    expect(labelRule).toContain('color: var(--muted)')
  })
})

describe('ChatPage active plan dock styles', () => {
  it('leaves scrolling to the task step list instead of clipping the whole plan card', () => {
    const dockRule = stylesheet.match(/\.composer-plan-dock\s*\{([^}]*)\}/g)?.at(-1) ?? ''

    expect(dockRule).toContain('max-height: none')
    expect(dockRule).toContain('align-items: flex-start')
    expect(dockRule).toContain('overflow: visible')
  })
})
