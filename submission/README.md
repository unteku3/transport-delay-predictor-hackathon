# Итоговый CSV

**[submission.csv](submission.csv)** — 151 прогноз от той же Optuna-модели на
69 признаках, которая работает в дашборде. Колонки: `sample_id;prediction`,
разделитель `;`, единица прогноза — секунды задержки.
[Manifest](submission.manifest.json) фиксирует хеш CSV и веса.
Файл совпадает с ранее переданным `submission_optuna_features_v2.csv`.

На платформу автоматически не отправлялся.

## Воспроизведение без обучения

Из исходной раздачи нужны только:

```text
validate/points.csv
validate/schedule_plan.csv
validate/traffic.csv
sample_submission.csv
```

```bash
.venv/bin/python scripts/generate_submission.py \
  --data-root /absolute/path/to/distribution \
  --output outputs/submission_reproduced.csv
```

Команда проверяет хеши исходников и весов, читает только разрешённые входы и план,
строит причинные признаки, проверяет идентификаторы и порядок строк.
Выходной путь должен быть новым. Скрытые целевые ответы не читаются.
