import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './styles.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

// ── PWA install prompt ────────────────────────────────────────────────────────
let deferredPrompt: any = null

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault()
  deferredPrompt = e

  const dismissed = sessionStorage.getItem('pwa-dismissed')
  if (dismissed) return

  const banner = document.createElement('div')
  banner.id = 'pwa-install-banner'
  banner.innerHTML = `
    <div class="pwa-banner-text">
      <strong>Instalar Apex Terminal</strong>
      Adicionar ao ecrã inicial como app
    </div>
    <button class="pwa-install-btn" id="pwa-install-btn">Instalar</button>
    <button class="pwa-dismiss-btn" id="pwa-dismiss-btn" aria-label="Fechar">✕</button>
  `
  document.body.appendChild(banner)

  document.getElementById('pwa-install-btn')?.addEventListener('click', async () => {
    banner.remove()
    if (deferredPrompt) {
      deferredPrompt.prompt()
      const { outcome } = await deferredPrompt.userChoice
      if (outcome === 'accepted') console.log('PWA installed')
      deferredPrompt = null
    }
  })

  document.getElementById('pwa-dismiss-btn')?.addEventListener('click', () => {
    banner.remove()
    sessionStorage.setItem('pwa-dismissed', '1')
  })
})

window.addEventListener('appinstalled', () => {
  deferredPrompt = null
  document.getElementById('pwa-install-banner')?.remove()
})
