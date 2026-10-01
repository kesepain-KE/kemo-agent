import { describe, expect, it } from 'vitest'
import {
  MAX_WIDGET_SOURCE_CHARS,
  MAX_WIDGETS_PER_MESSAGE,
  componentNames,
  envelopeSchema,
  propsSchemas,
} from './inlineWidgetSchemas'

describe('inline widget schema contract', () => {
  it('keeps protocol limits explicit and stable', () => {
    expect(MAX_WIDGET_SOURCE_CHARS).toBe(262_144)
    expect(MAX_WIDGETS_PER_MESSAGE).toBe(24)
  })

  it('registers a validator for every advertised component', () => {
    expect(componentNames.length).toBeGreaterThanOrEqual(39)
    for (const name of componentNames) {
      expect(propsSchemas[name]).toBeDefined()
    }
    expect(componentNames).toEqual(expect.arrayContaining([
      'form', 'confirm', 'approval', 'map', 'poll', 'rating', 'generic-card',
    ]))
  })

  it('keeps the envelope independent from component props validation', () => {
    const result = envelopeSchema.safeParse({
      protocol: 'kemo-ui',
      schema_version: 1,
      surface: 'inline',
      id: 'schema-contract',
      component: 'callout',
      props: { content: 'hello' },
    })
    expect(result.success).toBe(true)
  })
})
