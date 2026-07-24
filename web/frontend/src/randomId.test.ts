import { afterEach, describe, expect, it, vi } from 'vitest'
import { randomUUID } from './randomId'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('randomUUID', () => {
  it('优先使用浏览器原生 randomUUID', () => {
    const native = vi.fn(() => '123e4567-e89b-42d3-a456-426614174000')
    vi.stubGlobal('crypto', { randomUUID: native, getRandomValues: vi.fn() })

    expect(randomUUID()).toBe('123e4567-e89b-42d3-a456-426614174000')
    expect(native).toHaveBeenCalledOnce()
  })

  it('普通局域网 HTTP 下使用 getRandomValues 生成 UUID v4', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index))
      return bytes
    })
    vi.stubGlobal('crypto', { getRandomValues })

    const value = randomUUID()

    expect(value).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(value).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f')
    expect(getRandomValues).toHaveBeenCalledOnce()
  })

  it('没有安全随机源时明确报错而不是退化为 Math.random', () => {
    vi.stubGlobal('crypto', {})
    expect(() => randomUUID()).toThrow('不支持安全随机数生成')
  })
})

