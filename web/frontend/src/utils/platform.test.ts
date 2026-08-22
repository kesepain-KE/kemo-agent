import { describe, expect, it } from 'vitest'
import { isWindowsDesktop } from './platform'

describe('isWindowsDesktop', () => {
  it('只接受 Windows 桌面 UA', () => {
    expect(isWindowsDesktop('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')).toBe(true)
    expect(isWindowsDesktop('Mozilla/5.0 (Linux; Android 15; Mobile)')).toBe(false)
    expect(isWindowsDesktop('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)')).toBe(false)
    expect(isWindowsDesktop('Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)')).toBe(false)
    expect(isWindowsDesktop('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')).toBe(false)
  })
})
