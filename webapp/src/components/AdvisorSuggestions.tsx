import { advisorSuggestions } from '../advisorSuggestions'

export function AdvisorSuggestions({ shipId, onSelect }: { shipId: string; onSelect: (question: string) => void }) {
  return (
    <section className="advisor-suggestions" aria-labelledby="advisor-suggestions-title">
      <span id="advisor-suggestions-title">建議提問</span>
      <div>
        {advisorSuggestions(shipId).map((suggestion) => (
          <button
            key={suggestion.id}
            type="button"
            aria-label={`建議提問：${suggestion.question}`}
            title={suggestion.question}
            onClick={() => onSelect(suggestion.question)}
          >
            {suggestion.label}
          </button>
        ))}
      </div>
    </section>
  )
}
