interface StatusPillProps {
  text: string
  tone?: 'neutral' | 'good' | 'warn' | 'bad'
}

export function StatusPill({ text, tone = 'neutral' }: StatusPillProps) {
  return <span className={`pill pill-${tone}`}>{text}</span>
}
