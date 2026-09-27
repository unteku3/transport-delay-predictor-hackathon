# Yandex Cloud: демо для жюри

Конфигурация: Ubuntu 24.04, Intel Ice Lake (standard-v3), 2 vCPU × 50%,
4 ГБ RAM, network-ssd 20 ГБ, один публичный IPv4. Без GPU и прерывания.
Оценка по Billing API на 27.09.2026: `2×0.75 + 4×0.33 + 20×0.0199 + 0.26352`
= **3.48152 ₽/час**, **83.56 ₽/сутки**, **2506.69 ₽ за 30 дней**.
Исходящий трафик сверх бесплатного лимита оплачивается отдельно.
Источник тарифов: https://yandex.cloud/ru/docs/compute/pricing
и https://yandex.cloud/ru/docs/vpc/pricing.

## Установка

1. Создайте VM с конфигурацией выше; `cloud-init.yaml` устанавливает Docker из
   официальных Ubuntu-пакетов. Добавьте пользователя с вашим публичным SSH-ключом.
2. Разместите код без Git-истории в `/opt/predictor`, четыре входа из
   `data/README.md` — в `/srv/predictor-data`. Не отдавайте каталог данных через nginx.
3. В `/opt/predictor/.env` задайте:

```dotenv
DATA_ROOT=/srv/predictor-data
DEMO_HOST=your-public-domain
SPEED=10
REPLAY_SPLIT=test
```

4. Из `/opt/predictor`:

```bash
sudo docker compose -f compose.yaml -f deploy/compose.cloud.yaml up -d --build
sudo cp deploy/predictor-replay.service deploy/predictor-replay.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now predictor-replay.timer
```

Caddy публикует только 80/443, получает HTTPS-сертификат и проксирует dashboard.
API и WebSocket доступны через тот же домен; backend/emulator порты на хосте
привязаны к localhost. `/api/demo/control` закрыт на публичном прокси.
Сценарии what-if не изменяют движение и остаются доступны.

## Проверка

- `/` — дашборд; `/reference/` — инструкция жюри.
- `/docs`, `/openapi.json` — Swagger и схема backend.
- `/reference/ml-openapi.json` — схема ML.
- `/reference/code/` — сгенерированный PyDoc.
- `/api/health`, `/api/metrics` — состояние и измерения.

Smoke с управлением потоком запускайте на VM через localhost: он временно
отключает и восстанавливает TCP. На публичном адресе это намеренно запрещено.
Таймер начинает новую сессию после конца replay, останавливая emulator/backend
и поднимая их заново. Это сброс состояния и метрик между сессиями, не backfill.

## Расходы и остановка

Ресурсы тарифицируются, пока VM работает. Остановка прекращает оплату CPU/RAM;
диск остаётся платным. Удаление VM с auto-delete удаляет её загрузочный диск.
Перед окончательным удалением сохраните нужные конфигурации и результаты.
Динамический адрес может измениться после остановки VM — обновите DNS/DEMO_HOST.
Срок отключения согласуйте с датой проверки жюри; автоматически в дедлайн сдачи
VM не выключается, чтобы организаторы могли проверить решение позже.
