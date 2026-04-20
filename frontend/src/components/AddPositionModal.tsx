import { useEffect, useState, useRef } from 'react'
import { api } from '../api/client'

interface Props {
  portfolioId: string
  onClose: () => void
  onAdded: () => void
}

interface SearchResult {
  ticker: string
  name: string
  sector?: string
  current_price: number | null
}

export function AddPositionModal({ portfolioId, onClose, onAdded }: Props) {
  const [tickerQuery, setTickerQuery] = useState('')
  const [selectedAsset, setSelectedAsset] = useState<SearchResult | null>(null)
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [quantity, setQuantity] = useState('')
  const [entryPrice, setEntryPrice] = useState('')
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10))
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const searchTimeout = useRef<number | null>(null)

  // Search assets on query change (debounced)
  useEffect(() => {
    if (!tickerQuery || tickerQuery.length < 1 || selectedAsset?.ticker === tickerQuery.toUpperCase()) {
      setSearchResults([])
      setShowDropdown(false)
      return
    }
    if (searchTimeout.current) window.clearTimeout(searchTimeout.current)
    searchTimeout.current = window.setTimeout(async () => {
      try {
        const results = await api.get<SearchResult[]>(`/assets/search?q=${encodeURIComponent(tickerQuery)}`)
        setSearchResults(results)
        setShowDropdown(results.length > 0)
      } catch {
        setSearchResults([])
      }
    }, 200)
    return () => {
      if (searchTimeout.current) window.clearTimeout(searchTimeout.current)
    }
  }, [tickerQuery, selectedAsset])

  function handleSelect(asset: SearchResult) {
    setSelectedAsset(asset)
    setTickerQuery(asset.ticker)
    setShowDropdown(false)
    if (asset.current_price && !entryPrice) {
      setEntryPrice(asset.current_price.toFixed(2))
    }
  }

  async function handleSubmit(e: React.MouseEvent) {
    e.preventDefault()
    setError(null)
    const qty = parseFloat(quantity)
    const price = parseFloat(entryPrice)
    const ticker = selectedAsset?.ticker || tickerQuery.toUpperCase().trim()
    if (!ticker || !qty || !price || qty <= 0 || price <= 0) {
      setError('Preenche todos os campos obrigatórios.')
      return
    }
    setLoading(true)
    try {
      await api.post(`/portfolios/${portfolioId}/positions`, {
        ticker,
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
          <div className="form-field autocomplete-field">
            <label>Ticker *</label>
            <input
              type="text"
              value={tickerQuery}
              onChange={(e) => {
                setTickerQuery(e.target.value.toUpperCase())
                setSelectedAsset(null)
              }}
              onFocus={() => tickerQuery && setShowDropdown(searchResults.length > 0)}
              onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
              placeholder="Escreve para procurar (AAPL, NVDA...)"
              autoFocus
              autoComplete="off"
            />
            {showDropdown && searchResults.length > 0 && (
              <div className="autocomplete-dropdown">
                {searchResults.slice(0, 8).map((a) => (
                  <div
                    key={a.ticker}
                    className="autocomplete-item"
                    onMouseDown={(e) => { e.preventDefault(); handleSelect(a) }}
                  >
                    <div className="autocomplete-main">
                      <strong>{a.ticker}</strong>
                      <span className="autocomplete-name">{a.name}</span>
                    </div>
                    {a.current_price != null && (
                      <span className="autocomplete-price">${a.current_price.toFixed(2)}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {selectedAsset && (
              <div className="selected-asset">
                {selectedAsset.name} {selectedAsset.sector ? `· ${selectedAsset.sector}` : ''}
                {selectedAsset.current_price != null && (
                  <span> · preço atual ${selectedAsset.current_price.toFixed(2)}</span>
                )}
              </div>
            )}
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
          <button className="btn-secondary" onClick={onClose} disabled={loading}>Cancelar</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? 'A adicionar…' : 'Adicionar'}
          </button>
        </div>
      </div>
    </div>
  )
}
