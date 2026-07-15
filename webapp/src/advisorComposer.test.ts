import { describe, expect, it } from 'vitest'

import { shouldSubmitAdvisorComposer } from './advisorComposer'

describe('shouldSubmitAdvisorComposer', () => {
  it('submits plain Enter', () => {
    expect(shouldSubmitAdvisorComposer({ key: 'Enter', shiftKey: false, isComposing: false })).toBe(true)
  })

  it('keeps Shift+Enter for a newline', () => {
    expect(shouldSubmitAdvisorComposer({ key: 'Enter', shiftKey: true, isComposing: false })).toBe(false)
  })

  it('does not submit while an IME composition is being confirmed', () => {
    expect(shouldSubmitAdvisorComposer({ key: 'Enter', shiftKey: false, isComposing: true })).toBe(false)
  })
})
