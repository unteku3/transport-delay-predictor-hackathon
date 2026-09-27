# Dashboard

Frontend диспетчера с картой OpenLayers + OpenStreetMap, без mock-прогнозов:

- `layout.html` — шаблон интерфейса;
- `live.js` — REST/WebSocket-адаптер и отрисовка живых данных;
- `index.html` — собранный статический артефакт;
- `server.py` — локальный gateway без Docker;
- `nginx.conf`, `Dockerfile` — gateway для Compose.

После изменения `layout.html` или `live.js` пересоберите артефакт:

```bash
python3 scripts/build_dashboard.py
```
