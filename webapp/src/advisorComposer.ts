export type AdvisorComposerKey = {
  key: string
  shiftKey: boolean
  isComposing: boolean
}

export function shouldSubmitAdvisorComposer(event: AdvisorComposerKey) {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing
}
