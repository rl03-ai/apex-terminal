import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

interface PositionSummary {
  id: string
  ticker: string
  name: string
  status: string
  avg_cost: number
  quantity: number
  invested: number
  current_price: number
  current_value: number
  unrealised_pnl: number
  unrealised_pct: number
  realised_pnl: number
  total_pnl: number
}

interface Transaction {
  id: string
  type: 'buy' | 'sell'
  date: string
  quantity: number
  price: number
  value: number
  notes?: string
}

interface TxModalProps {
  portfolioId: string
  positionId: string
  defaultType: 'buy' | 'sell'
  currentPrice: number
  maxQuantity: number
  onClose: () => void
  onSaved: () => void
}

function TxModal({ portfolioId, positionId, defaultType, currentPrice, maxQuantity, onClose, onSaved }: TxModalProps) {
  const [type, setType] = useState<'buy' | 'sell'>(defaultType)
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState(currentPrice.toFixed(2))
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit() {
    setError(null)
    const qty = parseFloat(quantity)
    const px  = parseFloat(price)
    if (!qty || !px || qty <= 0 || px <= 0) {
      setError('Preenche quantidade e preço.')
      return
    }
    if (type === 'sell' && qty > maxQuantity + 1e-6) {
      setError(`Não podes vender mais do que tens (${maxQuantity.toFixed(4)}).`)
      return
    }
    setLoading(true)
    try {
      await api.post(`/portfolios/${portfolioId}/positions/${positionId}/transactions`, {
        type, quantity: qty, price: px, date, notes: notes || null,
      })
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro')
    } finally {
      setLoading(false)
    }
  }

  const value = (parseFloat(quantity) || 0) * (parseFloat(price) || 0)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{type === 'buy' ? 'Adicionar compra' : 'Registar venda'}</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <div className="tx-type-selector">
            <button
              className={`tx-type-btn ${type === 'buy' ? 'tx-type-active-buy' : ''}`}
              onClick={() => setType('buy')}
              type="button"
            >+ Compra</button>
            <button
              className={`tx-type-btn ${type === 'sell' ? 'tx-type-active-sell' : ''}`}
              onClick={() => setType('sell')}
              type="button"
            >− Venda</button>
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
                placeholder={type === 'sell' ? `Max ${maxQuantity.toFixed(4)}` : ''}
              />
              {type === 'sell' && (
                <button
                  className="btn-link-small"
                  onClick={() => setQuantity(String(maxQuantity))}
                  type="button"
                >Vender tudo ({maxQuantity.toFixed(4)})</button>
              )}
            </div>
            <div className="form-field">
              <label>Preço *</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
              />
            </div>
          </div>

          <div className="form-field">
            <label>Data</label>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>

          <div className="form-field">
            <label>Notas (opcional)</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Motivo, corretora, etc."
            />
          </div>

          {value > 0 && (
            <div className="invested-preview">
              Total {type === 'buy' ? 'investido' : 'recebido'}: <strong>${value.toFixed(2)}</strong>
            </div>
          )}

          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>Cancelar</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? '...' : (type === 'buy' ? 'Registar compra' : 'Registar venda')}
          </button>
        </div>
      </div>
    </div>
  )
}

export function PositionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [portfolioId, setPortfolioId] = useState<string>('')
  const [summary, setSummary] = useState<PositionSummary | null>(null)
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showTxModal, setShowTxModal] = useState<'buy' | 'sell' | null>(null)
  const [riskInfo, setRiskInfo] = useState<{ stop_price: number; stop_method: string; distance_to_stop_pct: number; risk_level: string; risk_reason: string } | null>(null)

  async function loadData() {
    if (!id) return
    try {
      setLoading(true)
      const portfolios = await api.get<{ id: string }[]>('/portfolios')
      let found: PositionSummary | null = null
      let pfId = ''
      for (const pf of portfolios) {
        try {
          const s = await api.get<PositionSummary>(`/portfolios/${pf.id}/positions/${id}/summary`)
          found = s
          pfId = pf.id
          break
        } catch { /* not in this portfolio */ }
      }
      if (!found) {
        setError('Posição não encontrada')
        return
      }
      setPortfolioId(pfId)
      setSummary(found)
      const txs = await api.get<Transaction[]>(`/portfolios/${pfId}/positions/${id}/transactions`)

      // Fetch risk info (stop-loss suggestion)
      try {
        const riskData: any = await api.get(`/portfolios/${pfId}/risk`)
        const posRisk = (riskData.position_risks || []).find((pr: any) => pr.position_id === id)
        if (posRisk) {
          setRiskInfo({
            stop_price: posRisk.stop_price,
            stop_method: posRisk.stop_method,
            distance_to_stop_pct: posRisk.distance_to_stop_pct,
            risk_level: posRisk.risk_level,
            risk_reason: posRisk.risk_reason,
          })
        }
      } catch { /* skip */ }
      setTransactions(txs)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadData() }, [id])

  async function handleDeleteTx(txId: string) {
    if (!confirm('Remover esta transacção?')) return
    try {
      await api.delete(`/portfolios/${portfolioId}/positions/${id}/transactions/${txId}`)
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro')
    }
  }

  if (loading) return (
    <div className="page-stack">
      <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#8ea0bb' }}>A carregar…</div>
    </div>
  )

  if (error || !summary) return (
    <div className="page-stack">
      <button className="back-btn" onClick={() => navigate(-1)}>← Voltar</button>
      <div className="card" style={{ padding: '2rem', textAlign: 'center', color: '#f87171' }}>
        {error || 'Erro ao carregar posição'}
      </div>
    </div>
  )

  const unrealised_color = summary.unrealised_pnl >= 0 ? '#22c55e' : '#f87171'
  const realised_color   = summary.realised_pnl >= 0 ? '#22c55e' : '#f87171'

  return (
    <div className="page-stack">
      <button className="back-btn" onClick={() => navigate(-1)}>← Voltar</button>

      <div className="card asset-header">
        <div>
          <div className="asset-ticker" style={{ cursor: 'pointer' }} onClick={() => navigate(`/asset/${summary.ticker}`)}>
            {summary.ticker}
          </div>
          <div className="asset-name">{summary.name}</div>
          <div className="asset-meta">
            <span className={`meta-chip`}>{summary.status === 'closed' ? 'FECHADA' : 'ABERTA'}</span>
          </div>
        </div>
        <div className="asset-score-big">
          <div className="asset-score-number" style={{ color: unrealised_color, fontSize: '2rem' }}>
            {summary.unrealised_pct >= 0 ? '+' : ''}{summary.unrealised_pct.toFixed(2)}%
          </div>
          <div className="muted small">P&L não realizado</div>
        </div>
      </div>

      <div className="stats-grid">
        <div className="card">
          <div className="sidebar-stat-label">Qtd atual</div>
          <div className="sidebar-stat-value">{summary.quantity.toFixed(4).replace(/\.?0+$/, '')}</div>
        </div>
        <div className="card">
          <div className="sidebar-stat-label">Preço médio</div>
          <div className="sidebar-stat-value">${summary.avg_cost.toFixed(2)}</div>
        </div>
        <div className="card">
          <div className="sidebar-stat-label">Preço atual</div>
          <div className="sidebar-stat-value">${summary.current_price.toFixed(2)}</div>
        </div>
        <div className="card">
          <div className="sidebar-stat-label">Investido</div>
          <div className="sidebar-stat-value">${summary.invested.toFixed(2)}</div>
        </div>
      </div>

      <div className="card">
        <div className="section-header"><h2>P&L</h2></div>
        <div className="pnl-grid">
          <div>
            <div className="pnl-label">Não realizado</div>
            <div className="pnl-value" style={{ color: unrealised_color }}>
              {summary.unrealised_pnl >= 0 ? '+' : ''}${summary.unrealised_pnl.toFixed(2)}
            </div>
            <div className="muted small">
              {summary.unrealised_pct >= 0 ? '+' : ''}{summary.unrealised_pct.toFixed(2)}% · valor ${summary.current_value.toFixed(2)}
            </div>
          </div>
          <div>
            <div className="pnl-label">Realizado (vendas)</div>
            <div className="pnl-value" style={{ color: realised_color }}>
              {summary.realised_pnl >= 0 ? '+' : ''}${summary.realised_pnl.toFixed(2)}
            </div>
            <div className="muted small">Lucro/prejuízo já concretizado</div>
          </div>
          <div>
            <div className="pnl-label">Total</div>
            <div className="pnl-value" style={{ color: summary.total_pnl >= 0 ? '#22c55e' : '#f87171' }}>
              {summary.total_pnl >= 0 ? '+' : ''}${summary.total_pnl.toFixed(2)}
            </div>
            <div className="muted small">Realizado + não realizado</div>
          </div>
        </div>
      </div>


      {/* Stop-loss & Risk */}
      {riskInfo && (
        <div className="card">
          <div className="section-header"><h2>🛡️ Gestão de risco</h2></div>
          <div className="stop-loss-display">
            <div className="stop-main">
              <div>
                <div className="stop-label">Stop-loss sugerido</div>
                <div className="stop-price">${riskInfo.stop_price.toFixed(2)}</div>
                <div className="muted small">
                  Método: <strong>{riskInfo.stop_method}</strong> · {riskInfo.distance_to_stop_pct.toFixed(1)}% abaixo do preço atual
                </div>
              </div>
              <div className={`risk-badge-large risk-${riskInfo.risk_level}`}>
                {riskInfo.risk_level === 'red' ? '🔴' : riskInfo.risk_level === 'yellow' ? '🟡' : '🟢'}
                <div className="risk-badge-label">
                  {riskInfo.risk_reason}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="section-header">
          <h2>Transacções</h2>
          <div className="toolbar-row">
            <button className="btn-buy" onClick={() => setShowTxModal('buy')}>+ Compra</button>
            {summary.quantity > 0 && (
              <button className="btn-sell" onClick={() => setShowTxModal('sell')}>− Venda</button>
            )}
          </div>
        </div>

        {transactions.length === 0 ? (
          <div className="empty-state-portfolio">
            <p className="muted">Sem transacções registadas.</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="position-table">
              <thead>
                <tr>
                  <th>Tipo</th>
                  <th>Data</th>
                  <th>Quantidade</th>
                  <th>Preço</th>
                  <th>Valor</th>
                  <th>Notas</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((tx) => (
                  <tr key={tx.id}>
                    <td>
                      <span className={`tx-badge tx-badge-${tx.type}`}>
                        {tx.type === 'buy' ? 'COMPRA' : 'VENDA'}
                      </span>
                    </td>
                    <td>{tx.date}</td>
                    <td>{tx.quantity.toFixed(4).replace(/\.?0+$/, '')}</td>
                    <td>${tx.price.toFixed(2)}</td>
                    <td>${tx.value.toFixed(2)}</td>
                    <td className="muted small">{tx.notes || '—'}</td>
                    <td>
                      <button className="btn-delete" onClick={() => handleDeleteTx(tx.id)}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showTxModal && (
        <TxModal
          portfolioId={portfolioId}
          positionId={id!}
          defaultType={showTxModal}
          currentPrice={summary.current_price}
          maxQuantity={summary.quantity}
          onClose={() => setShowTxModal(null)}
          onSaved={loadData}
        />
      )}
    </div>
  )
}
