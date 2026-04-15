interface ScoreBarProps {
  value: number
  max?: number
}

export function ScoreBar({ value, max = 100 }: ScoreBarProps) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="scorebar">
      <div className="scorebar-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}
