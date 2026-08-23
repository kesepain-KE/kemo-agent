import { getCompletionSoundUrl, playCompletionSoundFallback } from '../api/client'
import { isWindowsDesktop } from './platform'

const BROWSER_PLAYBACK_TIMEOUT_MS = 2_000

function stopBrowserPlayback(audio: HTMLAudioElement) {
  try {
    audio.pause()
  } catch {
    // Best effort: the Windows backend fallback remains available.
  }
  try {
    audio.removeAttribute('src')
    audio.load()
  } catch {
    // Some test or embedded browser implementations expose only play().
  }
}

async function playInBrowser(audio: HTMLAudioElement): Promise<boolean> {
  let timeout: ReturnType<typeof setTimeout> | undefined
  const browserPlayback = Promise.resolve()
    .then(() => audio.play())
    .then(
      () => true,
      () => false,
    )
  const timedPlayback = new Promise<boolean>((resolve) => {
    timeout = setTimeout(() => resolve(false), BROWSER_PLAYBACK_TIMEOUT_MS)
  })
  const played = await Promise.race([browserPlayback, timedPlayback])
  if (timeout !== undefined) clearTimeout(timeout)
  if (!played) stopBrowserPlayback(audio)
  return played
}

export async function playUserCompletionSound(user: string): Promise<boolean> {
  if (!user || !isWindowsDesktop()) return false
  if (typeof Audio !== 'undefined') {
    try {
      const audio = new Audio(getCompletionSoundUrl(user))
      if (await playInBrowser(audio)) return true
    } catch {
      // Continue with the Windows host fallback below.
    }
  }
  try {
    return (await playCompletionSoundFallback(user)).played
  } catch {
    return false
  }
}
