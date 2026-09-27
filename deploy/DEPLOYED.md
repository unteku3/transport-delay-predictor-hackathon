# Демо развёрнуто 27 сентября 2026

- Дашборд: https://dispatch.111.88.241.89.sslip.io/
- Инструкция жюри и результаты измерений: https://dispatch.111.88.241.89.sslip.io/reference/
- Swagger: https://dispatch.111.88.241.89.sslip.io/docs
- PyDoc: https://dispatch.111.88.241.89.sslip.io/reference/code/

VM Yandex Cloud, зона `ru-central1-a`.
Итоговый исходный код и cloud-overlay находятся в main. В runtime оставлена только Optuna-модель на 69 признаках.
Модель Optuna 69 признаков; веса и исходники сверены по SHA-256 после передачи.
2 vCPU × 50%, 4 ГБ RAM, SSD 20 ГБ: около 84 ₽/сутки с IP.
Контейнеры занимали около 416 МиБ RAM; вся VM — около 1 ГБ, диск — 5 из 19 ГБ.

Проверены HTTPS с доверенным сертификатом, REST, WebSocket snapshot/delta,
Браузер React/OpenLayers с 13 планами и what-if без ошибок, TCP disconnect/stale/reconnect.
100 HTTP-инференсов после 5 прогревов: p50 23.56 мс, p95 39.26 мс.
Это искусственный schema-valid вход с параллельным replay, не end-to-end SLA.
Таймер `predictor-replay.timer` активен: после конца потока создаётся новая сессия.
Внешний `/api/demo/control` возвращает 403; оператор управляет через SSH/localhost.

Демо оставлено включённым для проверки жюри.
