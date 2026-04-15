import { ReactNode } from 'react'

interface SectionCardProps {
  title: string
  right?: ReactNode
  children: ReactNode
}

export function SectionCard({ title, right, children }: SectionCardProps) {
  return (
    <section className="card">
      <div className="section-header">
        <h2>{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}
