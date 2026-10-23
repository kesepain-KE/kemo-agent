import { useEffect, useState } from 'react'
import { Pause, Play, RotateCcw } from 'lucide-react'
import type { InlineWidgetDefinition } from './inlineWidgetProtocol'
import { WidgetHeader } from './InlineWidgetPrimitives'
import styles from './InlineWidget.module.css'

const orbitColors = {
  blue: '#4d9cff',
  cyan: '#55d6d2',
  amber: '#ffb347',
  violet: '#b18cff',
  rose: '#ff7d9c',
  green: '#62d38a',
  slate: '#aab7c6',
} as const

export function OrbitVisualWidget({ widget }: { widget: Extract<InlineWidgetDefinition, { component: 'interactive-visual' }> }) {
  const { title, description, center, bodies, time_label: timeLabel } = widget.props
  const [playing, setPlaying] = useState(true)
  const [time, setTime] = useState(0)
  const [speed, setSpeed] = useState(1)
  const [selected, setSelected] = useState(0)
  const maxOrbit = Math.max(...bodies.map((body) => body.orbit))
  const orbitSpacing = Math.min(31, 205 / maxOrbit)
  const centerX = 320
  const centerY = 142
  const selectedBody = bodies[selected] || bodies[0]

  useEffect(() => {
    if (!playing) return undefined
    let frame = 0
    let previous = performance.now()
    const tick = (now: number) => {
      const elapsed = Math.min(80, now - previous)
      previous = now
      setTime((value) => (value + elapsed * 0.000035 * speed) % 1)
      frame = window.requestAnimationFrame(tick)
    }
    frame = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(frame)
  }, [playing, speed])

  return (
    <section className={styles.widget} aria-label={title || '交互式轨道可视化'}>
      <WidgetHeader title={title} description={description} status={{ label: '交互可视化', tone: 'brand' }} />
      <div className={styles.orbitStage}>
        <svg className={styles.orbitSvg} viewBox="0 0 640 282" role="img" aria-label={`${title || '轨道可视化'}，${bodies.length} 个对象`}>
          <defs>
            <radialGradient id={`orbit-glow-${widget.id}`}>
              <stop offset="0%" stopColor="#ffd36a" stopOpacity=".9" />
              <stop offset="100%" stopColor="#ffd36a" stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle cx={centerX} cy={centerY} r="36" fill={`url(#orbit-glow-${widget.id})`} />
          {Array.from({ length: maxOrbit }, (_, index) => (
            <circle className={styles.orbitRing} key={index} cx={centerX} cy={centerY} r={(index + 1) * orbitSpacing} />
          ))}
          <circle className={styles.orbitCenter} cx={centerX} cy={centerY} r="7" />
          <text className={styles.orbitCenterLabel} x={centerX} y={centerY + 24} textAnchor="middle">{center?.label || '中心'}</text>
          {bodies.map((body, index) => {
            const angle = ((body.angle || 0) * Math.PI) / 180 + (time * Math.PI * 2) / (body.period || 1)
            const x = centerX + Math.cos(angle) * body.orbit * orbitSpacing
            const y = centerY + Math.sin(angle) * body.orbit * orbitSpacing * .6
            const active = index === selected
            return (
              <g key={`${body.label}-${index}`}>
                {active ? <circle className={styles.orbitBodyHalo} cx={x} cy={y} r={(body.radius || 6) + 7} /> : null}
                <circle
                  className={styles.orbitBody}
                  cx={x}
                  cy={y}
                  r={body.radius || 6}
                  fill={orbitColors[body.color || 'blue']}
                  tabIndex={0}
                  role="button"
                  aria-label={body.label}
                  onClick={() => setSelected(index)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      setSelected(index)
                    }
                  }}
                />
              </g>
            )
          })}
        </svg>
        <div className={styles.orbitLegend} aria-label="可视化对象">
          {bodies.map((body, index) => (
            <button
              type="button"
              className={`${styles.orbitLegendItem} ${index === selected ? styles.orbitLegendActive : ''}`}
              key={`${body.label}-legend`}
              onClick={() => setSelected(index)}
            >
              <i style={{ background: orbitColors[body.color || 'blue'] }} />
              <span>{body.label}</span>
            </button>
          ))}
        </div>
      </div>
      <div className={styles.orbitControls}>
        <button type="button" className={styles.orbitControlButton} onClick={() => setPlaying((value) => !value)} aria-label={playing ? '暂停动画' : '播放动画'}>
          {playing ? <Pause size={14} /> : <Play size={14} />}
          {playing ? '暂停' : '播放'}
        </button>
        <button type="button" className={styles.orbitControlButton} onClick={() => setTime(0)} aria-label="重置动画">
          <RotateCcw size={14} />重置
        </button>
        <label className={styles.orbitRange}>
          <span>{timeLabel || '时间'}</span>
          <input type="range" min="0" max="100" step="1" value={Math.round(time * 100)} onChange={(event) => setTime(Number(event.target.value) / 100)} aria-label={timeLabel || '时间进度'} />
        </label>
        <label className={styles.orbitSpeed}>
          <span>速度</span>
          <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} aria-label="动画速度">
            {[.25, .5, 1, 2, 4].map((value) => <option value={value} key={value}>{value}x</option>)}
          </select>
        </label>
      </div>
      {selectedBody ? (
        <div className={styles.orbitDetail}>
          <strong>{selectedBody.label}</strong>
          <span>第 {selectedBody.orbit} 轨道 · 周期 {selectedBody.period || 1} 单位</span>
          {selectedBody.detail ? <p>{selectedBody.detail}</p> : null}
        </div>
      ) : null}
    </section>
  )
}

