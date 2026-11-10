# Проверка кандидата к выпуску

## Дополнение: ревью и уборка GitHub, 10 ноября

После исходной проверки исправлен подтверждённый regression: отрицательная
фраза могла завершить задачу. Теперь сообщения с отрицанием не изменяют статус
задач автоматически, включая смешанные фразы; доступны уточнение и кнопки.
Тест на временной SQLite сначала воспроизвёл ошибку, затем прошёл после правки.
Связанные проверки: 23 passed. Полный локальный offline pytest: **561 passed,
1 skipped**, два предупреждения зависимостей. Mypy проверил 51 файл; Ruff check
и format для изменённых Python-файлов прошли. PostgreSQL/Docker локально заново
не запускались; CI точного отправленного коммита проверяется отдельно.

README обновлён; иллюстрация содержит только вымышленные данные. Из дерева PR
исключены 26 ранее отслеживаемых файлов `outputs/`, при этом их локальное
содержимое сохранено. Имена в продуктовой документации и абсолютные пути в
карте/инвентаризации обезличены. Это **не удаляет данные из прежних коммитов,
main, PR diff, GitHub-кэшей или чужих клонов**. Переписывание истории и смена
видимости репозитория не выполнялись. Контакт оператора на публичных страницах
сохранён; настройки интеграций и секреты не менялись.

На GitHub включена защита main: обязательны quality/container/secrets,
актуальность PR относительно main, запрет force-push и удаления; администратор
тоже подчиняется правилам. Удалены 13 старых удалённых веток: 10 полностью
входили в main, ещё три содержали эквивалентные принятым PR патчи. Локальные
ветки и worktree сохранены. Семь Dependabot PR остаются отдельной очередью
обновлений, а не разрешением на массовое принятие новых major-версий.

Рабочая сборка не обновлялась. Нижеследующие результаты описывают предыдущий
артефакт кандидата и не заменяют проверку нового образа перед выпуском.

## Исходная проверка артефакта

Дата: **2026-11-10**, Europe/Kyiv. Ветка: `agent/consolidate-family-life-os-20261109`.
База объединения: `786ca0d`. Исправления сборки и проверки зафиксированы
локальным коммитом вместе с этим отчётом. Push и deploy не выполнялись.

## Результат и границы

Локальные проверки кандидата пройдены на Linux/Python 3.12.13, включая установленный
wheel приложения, PostgreSQL и контейнерное сканирование. Рабочий бот, его база,
переменные Railway и Google Sheets не изменялись. Telegram polling не запускался.

**Открытый пункт перед выпуском:** побайтовая сверка действующего контейнера.
SSH/SFTP к `ssh.railway.com:22` из текущей сети завершается тайм-аутом, в том числе
из Docker. База `3312fa3` подтверждена метаданными Railway, но пока не сверена с
фактическими файлами production. Локальные проверки этого не доказывают.
Подготовлен read-only `scripts/capture_railway_attestation.py`.

Google/Oura/LLM и отправка Telegram проверены тестами с подменёнными провайдерами.
Реальные OAuth-токены, запись расходов, сообщения и внешняя синхронизация на
production не проверялись. Выкладка и smoke после неё — отдельный этап.

## Проверенные результаты

| Проверка | Результат |
|---|---|
| Docker build | Успешно, linux/amd64, Python 3.12.13, appuser |
| Полный pytest на установленном wheel | **558 passed, 1 skipped**, 44,04 с |
| Покрытие с ветвлениями | **82,69%**, порог CI 80% пройден |
| Пропущенный offline-тест миграций | Отдельно на PostgreSQL: migration-модуль **3 passed, 0 skipped** |
| Alembic upgrade → check → downgrade base → upgrade → check | Пройдено, PostgreSQL 16 / pgvector 0.8.0; 26 таблиц со служебной Alembic |
| Bootstrap двух тестовых пользователей дважды | Создано 2, затем 0; итого 2 пользователя и 1 семья |
| HTTP без Telegram | `/live`, `/health`, `/`, `/privacy`: 200 |
| Хеши установленного runtime | 69 файлов совпали с checkout после нормализации CRLF/LF |
| pip check | Пройден |
| pip-audit runtime / dev | 58 / 95 пакетов, 0 уязвимостей |
| Trivy 0.70.0, как в закреплённом CI action | HIGH/CRITICAL, ignore-unfixed: true, exit 0 после узкой VEX-оценки |
| Ruff check / format | Пройдены |
| Mypy app | Пройден, 51 файл |
| Bandit app | Пройден; прежнее предупреждение о лишнем nosec |
| Gitleaks 8.24.3, как в закреплённом CI action | Экспорт исходников и история ветки: 0 оставшихся находок после оценки |
| Отрицательные проверки исключений | VEX: 3 regression tests; Gitleaks обнаруживает похожие значения вне допустимых строк |

Полный pytest запрещает исходящие socket-подключения и dotenv. Единственный skip
требует отдельную PostgreSQL; эта проверка реально выполнена на финальном образе.
Предупреждения зависимостей: будущая замена httpx в Starlette TestClient и выбор
fixture loop scope pytest-asyncio. Ошибок выполнения нет.
Hosted GitHub Actions не запускались: выполнены соответствующие локальные проверки.

## Какие файлы изменены

- `pyproject.toml`: существующая зависимость Pillow обновлена до диапазона 13.x.
- `Dockerfile`: pip 26.3 в builder/runner; сброс кеша слоя системных пакетов через
  SYSTEM_PACKAGES_REFRESH=2026-11-10. Получены исправления apt, OpenSSL, PCRE и zlib.
- `scripts/run_offline_tests.py`: writable-путь coverage при запуске от appuser.
- `scripts/validate_release_database.py`: миграции и bootstrap; отказывается работать
  без ENVIRONMENT=test и строго заданного адреса validation-pg/тестовой БД.
- `scripts/capture_railway_attestation.py`: чтение хешей через установленный Railway CLI.
- `.vex/pip.openvex.json`, `scripts/check_pip_vendor_surface.py`,
  `tests/test_pip_vendor_assessment.py`, `.github/workflows/ci.yml`:
  оценка отсутствующего уязвимого кода и обязательная проверка её условий перед Trivy.
- `.gitleaks.toml`: только полные SHA-256 строки файлов в трёх манифестах.
- `.gitleaksignore`: только fingerprint старой тестовой заглушки commit `8059665`,
  `.env.test.example:5`. Она декодируется в 34 байта, содержит тестовый маркер и
  не является допустимым 32-байтовым ключом Fernet. Значение не копировалось в отчёт.
- `docs/operations/CONSOLIDATION.md`, `RELEASE.md`, `integration-manifest.json`,
  этот отчёт и `release-validation.json`: фактический статус и состав кандидата.

Код приложения в `app/`, миграции, `main.py` и .env-файлы на этом этапе не менялись.

### Почему Trivy требует VEX

Сканер читает запись setuptools 70.3.0 в pip/_vendor/vendor.txt и сообщает
CVE-2025-47273. Она относится к setuptools.package_index.PackageIndex.download.
В runtime нет установленного setuptools и нет package_index.py; pip оставляет
только pkg_resources. Guard перед сканером закрепляет pip 26.3, наличие ожидаемой
vendoring metadata и отсутствие уязвимого модуля. Изменение условий останавливает
CI до VEX. Оценка ограничена именами образов приложения, одним subcomponent и CVE.

Основания: [политика vendoring pip](https://pip.pypa.io/en/stable/development/vendoring-policy/),
[advisory setuptools](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf).
Pillow: [release notes 13.0.0](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html).
Проверены и явное --vex, и TRIVY_VEX из CI. При сканировании безымянного tar OCI-имя
не сопоставляется; для воспроизведения нужен именованный образ.

## Точный артефакт и повторение

Метка `family-life-os:release-candidate-20261109` — кандидат, не production.

- OCI index / Docker image ID: `sha256:640b6f3ebea844623ffd10f10ef9c350902f742d238ce0dd1192aaae817de070`.
- Resolved config ID: `sha256:afa3e13677d2dc703c7fa53618d9c26ffc9af0b0d44d15396036b8aa1c34b71a`.
- Platform manifest: `sha256:108d9d65bf40be549174a7e2540b62c39130afcd5c861cb4c7d799a45a8a5267`.
- Python 3.12.13, Pillow 13.0.0, pip 26.3.
- Все установленные пакеты и runtime-хеши: `release-validation.json`.
  Исходники и конфигурация сборки: `integration-manifest.json`.

Диапазоны зависимостей пока не заменены lock-файлом. Новая сборка из тех же
исходников может получить другие пакеты; её нужно сверить с этим манифестом и
повторить проверки либо выпускать именно проверенный артефакт.

Полные доказательства: игнорируемый `outputs/release-validation/`. Их имена и SHA-256
записаны в `release-validation.json`. Там сохранены build log, JUnit, coverage,
миграции, HTTP smoke, pip-audit, Trivy, Gitleaks и отрицательные контроли.

Тестовый образ `family-life-os:release-candidate-tests-20261109` основан на финальном
runtime; dev-пакеты установлены поверх wheel. app не подмонтируется. Для последнего
полного запуска read-only подмонтированы текущие tests/ и scripts/, сеть отключена.
Контекст тестового образа сохранён в outputs/release-validation/test-context/.

PostgreSQL проверена в отдельной internal Docker-сети без опубликованных портов,
с синтетическими идентификаторами и tmpfs. Пользовательская локальная и рабочая БД
не использовались. После проверки тестовый PostgreSQL остановлен.

Для недостающей read-only сверки, из этого worktree, когда доступен SSH:

```powershell
rtk proxy ..\FAMILY-AI-LIFE-OS\.venv\Scripts\python.exe -B scripts/capture_railway_attestation.py
```

Скрипт сохраняет outputs/release-validation/runtime-attestation.json, отказывается
его перезаписывать и не читает окружение контейнера. Затем нужно сравнить результат
с восстановленной deployed-source-3312fa3 и учесть различия. Перед и после чтения
проверить deployment ID: сравнение должно относиться к одной сборке.

На последней read-only проверке Railway действовал прежний deployment
`a6b37a8e-07dd-45d7-9702-7beef42270ac`, SUCCESS, ревизия `3312fa3`.
