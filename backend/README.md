# Backend

Потоковая часть системы:

- `app.py`, `state.py` — FastAPI, WebSocket, causal state и журнал прогнозов;
- `ndtp.py` — декодирование NDTP и replay-расширение;
- `emulator/` — исторический CSV → NDTP replay;
- `contracts.py` — публичные API-контракты;
- `data.py` — каталог разрешённых входов и DataGuard.

Запуск всего стека выполняется из корня: `docker compose up --build -d`.
Полный контракт и ограничения описаны в [`docs/LIVE_SYSTEM.md`](../docs/LIVE_SYSTEM.md).
