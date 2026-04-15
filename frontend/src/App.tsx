import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DashboardPage } from './pages/DashboardPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { PositionDetailPage } from './pages/PositionDetailPage'
import { ScannerPage } from './pages/ScannerPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="scanner" element={<ScannerPage />} />
        <Route path="portfolio" element={<PortfolioPage />} />
        <Route path="positions/:id" element={<PositionDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
