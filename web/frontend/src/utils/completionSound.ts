import {
  getCompletionSoundUrl,
  getFailureSoundUrl,
  playCompletionSoundFallback,
  playFailureSoundFallback,
} from '../api/client'
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

export type RunSoundKind = 'completion' | 'failure'

export async function playUserRunSound(user: string, kind: RunSoundKind): Promise<boolean> {
  if (!user || !isWindowsDesktop()) return false
  const soundUrl = kind === 'failure' ? getFailureSoundUrl(user) : getCompletionSoundUrl(user)
  const fallback = kind === 'failure' ? playFailureSoundFallback : playCompletionSoundFallback
  if (typeof Audio !== 'undefined') {
    try {
      const audio = new Audio(soundUrl)
      if (await playInBrowser(audio)) return true
    } catch {
      // Continue with the Windows host fallback below.
    }
  }
  try {
    return (await fallback(user)).played
  } catch {
    return false
  }
}

export async function playUserCompletionSound(user: string): Promise<boolean> {
  return playUserRunSound(user, 'completion')
}

export async function playUserFailureSound(user: string): Promise<boolean> {
  return playUserRunSound(user, 'failure')
}
