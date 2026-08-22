import { getCompletionSoundUrl, playCompletionSoundFallback } from '../api/client'
import { isWindowsDesktop } from './platform'

export async function playUserCompletionSound(user: string): Promise<boolean> {
  if (!user || !isWindowsDesktop()) return false
  if (typeof Audio !== 'undefined') {
    try {
      const audio = new Audio(getCompletionSoundUrl(user))
      await audio.play()
      return true
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
