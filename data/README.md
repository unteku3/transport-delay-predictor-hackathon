# Входные данные

Скачайте [раздачу организаторов](https://disk.yandex.ru/d/CA6tsj4aJJ4Aaw).
CSV остаются локальными и подключаются в контейнеры только для чтения.

Для стандартного replay нужны четыре файла относительно `DATA_ROOT`:

```text
train/schedule.csv
validate/points.csv
test/traffic.csv
labels/labels_test.csv
```

В `.env` задайте абсолютный `DATA_ROOT` или оставьте `.` для корня проекта.
Для replay по train дополнительно нужны `train/traffic.csv` и `labels/labels_train.csv`.
На облачной VM загружены только четыре входа стандартного replay.

Для воспроизведения итогового CSV — [отдельный список](../submission/README.md).
Для полного pytest достаточно стандартных replay-входов, размещённых в корне проекта.
`source_hashes.json` хранит контрольные хеши раздачи, а не телеметрию.

План читается через DataGuard / read_plan; скрытые факты validate не извлекаются.
`test/traffic.csv` и `validate/traffic.csv` совпадают — это не независимые дни.
Исходные файлы не изменять.
