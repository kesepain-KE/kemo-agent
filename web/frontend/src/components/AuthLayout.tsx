import { useLayoutEffect, type ReactNode } from 'react'
import { ShieldCheck } from 'lucide-react'
import { useUiStore } from '../store/ui'
import styles from '../pages/AuthPage.module.css'

export function AuthLayout({ children }: { children: ReactNode }) {
  const theme = useUiStore(state => state.theme)
  const fontSize = useUiStore(state => state.fontSize)
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.dataset.fontSize = fontSize
    document.documentElement.dataset.fontsize = fontSize === 'small' ? 'compact' : fontSize === 'large' ? 'large' : 'default'
  }, [theme, fontSize])

  return (
    <main className={styles.screen}>
      <section className={styles.panel} aria-label="Web 访问认证">
        <header className={styles.brand}>
          <span className={styles.logo}>
            <img src="/kemo-agent.jpg" width={571} height={568} alt="" />
          </span>
          <div className={styles.brandCopy}>
            <strong>kemo-agent</strong>
            <span>Personal Agent Runtime</span>
          </div>
        </header>
        {children}
        <footer className={styles.footer}>
          <ShieldCheck size={15} aria-hidden="true" />
          <p><span>认证信息仅用于建立安全会话，</span><span>不会进入智能体上下文。</span></p>
        </footer>
      </section>
    </main>
  )
}
