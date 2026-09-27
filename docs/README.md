# Документация итогового решения

- [Архитектура и запуск](LIVE_SYSTEM.md).
- [Соответствие заданию и ограничения](HACKATHON.md).
- [Backend OpenAPI](api/backend.json), [ML OpenAPI](api/ml.json).
- [Облачное развёртывание](../deploy/README.md), [адреса и проверки](../deploy/DEPLOYED.md).
- [Очистка](../cleaning/README.md), [диагностические флаги](../cleaning/FLAGS.md).
- [Спецификация NDTP](Emulator-and-Telematic-Packets-Specification.md).
- [Исходные правила датасета](DATASET_RULES_ORIGINAL.md).

Генерация документации по текущему коду:

```bash
.venv/bin/python scripts/build_code_docs.py
.venv/bin/python scripts/export_openapi.py
```

Страницы PyDoc лежат в `deploy/site/code/`; OpenAPI — в `docs/api/`.
Опубликованная [инструкция для жюри](https://dispatch.111.88.241.89.sslip.io/reference/)
содержит сценарий проверки, ссылки на API и измеренную производительность.
Публикуется только итоговое решение; исследовательский архив не включён.
