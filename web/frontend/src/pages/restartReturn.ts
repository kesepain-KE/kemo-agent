import { getHealth } from '../api/client'
import { chatSessionUrl, readRememberedSession } from '../sessionNavigation'

/** 重启后等待原端口恢复，再加载聊天页；卸载配置页会取消等待。 */
export function scheduleRestartReturn(
  user: string,
  sessionId: string,
  onFailure: () => void,
  health = getHealth,
  navigate = (url: string) => window.location.replace(url),
) {
  const target = chatSessionUrl(user, sessionId || readRememberedSession(user))
  const deadline = Date.now() + 60_000
  let disposed = false
  let timer: number
  const poll = async () => {
    try {
      await health()
      if (!disposed) navigate(target)
    } catch {
      if (disposed) return
      if (Date.now() >= deadline) onFailure()
      else timer = window.setTimeout(() => { void poll() }, 1_000)
    }
  }
  timer = window.setTimeout(() => { void poll() }, 4_000)
  return () => { disposed = true; window.clearTimeout(timer) }
}
