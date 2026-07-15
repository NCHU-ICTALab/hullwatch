# Oi! Hullwatch React Dashboard

Bridge Ops 介面的 React + Vite + TypeScript 實作。開發時 `/api` 會 proxy 到
`http://127.0.0.1:8777`；production build 由 FastAPI 或 Docker 提供。

```powershell
npm install
npm run dev
npm run build
npm run lint
```

需求與進度見 `../docs/frontend-redesign-spec.md` 及
`../docs/frontend-implementation-worklog.md`。
