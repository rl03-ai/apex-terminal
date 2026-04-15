# Apex Frontend Minimal

Frontend mínimo em React + Vite para visualizar:
- dashboard
- scanner
- carteira
- detalhe da posição

## Requisitos
- Node 20+
- backend Apex a correr localmente

## Instalação
```bash
npm install
cp .env.example .env
npm run dev
```

## Configuração
Define a URL do backend em `.env`:
```env
VITE_API_BASE_URL=http://localhost:8000
```

## Rotas esperadas no backend
- `GET /scanner/top-opportunities`
- `GET /scanner/results?scanner_type=...&min_score=...`
- `GET /portfolios`
- `GET /portfolios/{id}/positions`
- `GET /positions/{id}`
- `GET /positions/{id}/history`
- `GET /positions/{id}/scenarios`
- `GET /alerts`

## Notas
O projeto foi mantido simples para servir de base. A UI assume que o backend devolve JSON já pronto para consumo. Se os nomes dos campos diferirem ligeiramente, basta ajustar `src/api/endpoints.ts` e os `types`.
