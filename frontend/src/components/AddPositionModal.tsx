import { useState } from 'react'
import { api } from '../api/client'

interface Props {
  portfolioId: string
  onClose: () => void
  onAdded: () => void
}

export function AddPositionModal({ portfolioId, onClose, onAdded }: Props) {
  const [ticker, setTicker] = useState('')
  const [quantity, setQuantity] = useState('')
  const [entryPrice, setEntryPrice] = useState('')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.MouseEvent) {
    e.preventDefault()
    setError(null)

    const qty = parseFloat(quantity)
    const price = parseFloat(entryPrice)
    if (!ticker || !qty || !price || qty <= 0 || price <= 0) {
      setError('Preenche todos os campos obrigatórios.')
      return
    }

    setLoading(true)
    try {
      await api.post(`/portfolios/${portfolioId}/positions`, {
        ticker: ticker.toUpperCase().trim(),
        quantity: qty,
        entry_price: price,
        entry_date: entryDate,
        notes: notes || null,
      })
      onAdded()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao adicionar posição')
    } finally {
      setLoading(false)
    }
  }

  const invested = (parseFloat(quantity) || 0) * (parseFloat(entryPrice) || 0)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Adicionar Posição</h2>
          <button className="modal-close" onClick={onClose} aria-label="Fechar">✕</button>
        </div>

        <div className="modal-body">
          <div className="form-field">
            <label>Ticker *</label>
            <input
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              placeholder="AAPL, NVDA, MSFT..."
              autoFocus
            />
          </div>

          <div className="form-row">
            <div className="form-field">
              <label>Quantidade *</label>
              <input
                type="number"
                step="0.0001"
                min="0"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="10"
              />
            </div>
            <div className="form-field">
              <label>Preço entrada *</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
                placeholder="180.50"
              />
            </div>
          </div>

          <div className="form-field">
            <label>Data de entrada</label>
            <input
              type="date"
              value={entryDate}
              onChange={(e) => setEntryDate(e.target.value)}
            />
          </div>

          <div className="form-field">
            <label>Notas (opcional)</label>
            <textarea
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Tese de investimento, alvo, etc."
            />
          </div>

          {invested > 0 && (
            <div className="invested-preview">
              Total investido: <strong>${invested.toFixed(2)}</strong>
            </div>
          )}

          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Cancelar
          </button>
          <button className="btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? 'A adicionar…' : 'Adicionar'}
          </button>
        </div>
      </div>
    </div>
  )
}
