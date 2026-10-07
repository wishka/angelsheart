# CI/CD: ветки, проверки, выкладка, выпуски

## Ветки

```
feature/*, fix/*  ──PR──▶  develop  ──PR──▶  main  ──тег vX.Y.Z──▶ выпуск Android
                             │                 │
                             ▼                 ▼
                         staging           production
                     (автоматически)   (после подтверждения)
```

| Ветка | Назначение | Куда уходит |
|---|---|---|
| `main` | то, что работает у людей | боевой сервер, после подтверждения в GitHub |
| `develop` | собранное и проверенное, ждёт выпуска | тестовый сервер, сразу |
| `feature/<что>` | новая функция, от `develop` | никуда; PR в `develop` |
| `fix/<что>` | исправление, от `develop` | никуда; PR в `develop` |
| `hotfix/<что>` | срочное исправление боевого, от `main` | PR в `main`, затем `main` → `develop` |

`main` и `develop` защищены: прямой push запрещён, слияние — только
pull request с зелёными проверками (`scripts/setup-github.sh`).

### Обычный цикл

```bash
git checkout develop && git pull
git checkout -b feature/chat-reactions
# ... работа, коммиты ...
git push -u origin feature/chat-reactions
gh pr create --base develop          # проверки идут автоматически
# слияние → develop выкладывается на тестовый сервер
```

Когда на тестовом всё проверено — PR `develop → main`. После слияния
выкладка в production ждёт подтверждения: **Actions → Deploy → Review
deployments → Approve**.

### Срочное исправление

```bash
git checkout main && git pull
git checkout -b hotfix/transfer-rounding
# ... исправление ...
gh pr create --base main
# после выкладки — вернуть исправление в develop:
gh pr create --base develop --head main --title "main → develop"
```

## Что проверяется (`.github/workflows/ci.yml`)

На каждый pull request и push в рабочие ветки:

| Проверка | Что делает |
|---|---|
| `server (sqlite)` | миграции соответствуют моделям; все тесты |
| `server (postgres)` | все тесты на PostgreSQL 16 — как на боевом |
| `android (debug)` | сборка отладочного APK; lint (пока не блокирует) |

Отладочный APK каждой проверки — в артефактах запуска
(`angelsheart-debug-apk`): его можно поставить и посмотреть изменения
до слияния.

## Выкладка сервера (`deploy.yml`, `deploy/deploy.sh`)

Push в `develop` или `main` → те же проверки → выкладка:

1. код уезжает на сервер rsync'ом в новый каталог `releases/<номер>`;
2. пока работает прежняя версия: зависимости (окружение Python
   пересоздаётся, только если изменился `requirements.txt`),
   `check --deploy`, **дамп базы**, `migrate`, `collectstatic`.
   Ошибка на любом шаге — выкладка останавливается, сайт не замечает;
3. ссылка `current` атомарно переключается, Gunicorn перезапускается;
4. `/health/` должен ответить номером нового релиза — изнутри сервера
   и снаружи через nginx. Не ответил — автоматический возврат на
   прежний релиз.

Хранятся 5 последних релизов и 10 последних дампов базы.

### Правило миграций

Миграции применяются **до** переключения, пока работает прежний код,
и при откате не возвращаются. Поэтому каждая миграция должна быть
совместима с предыдущей версией кода:

* новое поле — с значением по умолчанию или `null=True`;
* удаление или переименование поля — в два выпуска: сначала код
  перестаёт его использовать, потом отдельный выпуск его удаляет;
* тяжёлые миграции данных — отдельной командой, не в `migrate`.

### Откат

**Actions → Rollback → Run workflow**: окружение и (необязательно)
номер релиза; пусто — предыдущий. Занимает секунды: код старого
релиза уже лежит на сервере. Вручную на сервере —
`bash /srv/angelsheart/current/deploy/rollback.sh`.

Если нужно вернуть и схему базы — дамп перед каждой выкладкой лежит в
`/srv/angelsheart/backups/<релиз>.sql.gz`.

## Выпуск Android (`release.yml`)

```bash
git checkout main && git pull
git tag v1.2.0
git push origin v1.2.0
```

Тег должен стоять на `main` — иначе выпуск остановится. Дальше, после
подтверждения в окружении production:

* подписанные ключом выпуска `angelsheart-1.2.0.apk` и `.aab`;
* проверка подписи `apksigner`;
* GitHub Release со списком изменений (из названий PR), файлами
  и `SHA256SUMS.txt`;
* APK на боевой сервер: `shared/app-release/angelsheart.apk` —
  отсюда его отдаёт сайт; 5 прежних версий — в `archive/`.

**Номер версии** — из тега по semver: `vMAJOR.MINOR.PATCH`. Код версии
для Android — `MAJOR×10000 + MINOR×100 + PATCH` (`v1.2.3` → `10203`):
растёт с каждым выпуском, как требуют магазины.

Release-сборка смотрит на рабочий сервер (`PRODUCTION_API_URL`) и
работает только по HTTPS; отладочная — на `127.0.0.1` и разрешает
HTTP (`src/debug/res/xml/network_security_config.xml`).

### RuStore (на будущее)

`.aab` уже прикладывается к каждому выпуску. Когда появится аккаунт
разработчика RuStore: загрузка первой версии — вручную через консоль,
дальше — шаг в `release.yml` через RuStore Public API (ключ API —
секрет `RUSTORE_KEY_ID` / `RUSTORE_PRIVATE_KEY` окружения production).

## Секреты и переменные

Задаются в **Settings → Environments → staging / production** (или
`gh secret set … --env …` / `gh variable set … --env …`). У staging и
production свои значения — это могут быть разные серверы.

### Секреты

| Имя | Окружение | Что это |
|---|---|---|
| `DEPLOY_SSH_KEY` | оба | закрытый SSH-ключ пользователя `deploy` (deploy/README.md, п. 6) |
| `DEPLOY_KNOWN_HOSTS` | оба | вывод `ssh-keyscan -H <сервер>` |
| `ANDROID_KEYSTORE_BASE64` | production | ключ выпуска: `base64 -w0 release.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | production | пароль хранилища ключей |
| `ANDROID_KEY_ALIAS` | production | имя ключа в хранилище |
| `ANDROID_KEY_PASSWORD` | production | пароль ключа |
| `GOOGLE_SERVICES_JSON` | production | содержимое `google-services.json` (push); без него выпуск без уведомлений |

Ключ выпуска создаётся один раз и хранится вне репозитория (менеджер
паролей + резервная копия). **Потеряли — обновления больше не встанут
поверх установленного приложения.**

```bash
keytool -genkeypair -v -keystore release.jks -alias angelsheart \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 release.jks | gh secret set ANDROID_KEYSTORE_BASE64 --env production
```

### Переменные

| Имя | Пример (production / staging) | Зачем |
|---|---|---|
| `DEPLOY_HOST` | `angel-helper.ru` | куда выкладывать |
| `DEPLOY_USER` | `deploy` | пользователь на сервере |
| `DEPLOY_PORT` | `22` | порт SSH |
| `DEPLOY_PATH` | `/srv/angelsheart` / `/srv/angelsheart-staging` | каталог приложения |
| `SERVICE_NAME` | `angelsheart` / `angelsheart-staging` | сервис systemd |
| `HEALTH_URL` | `http://127.0.0.1:8000/health/` / `…:8001/health/` | Gunicorn изнутри сервера |
| `HEALTH_HOST` | `angel-helper.ru` | заголовок Host для проверки (из ALLOWED_HOSTS) |
| `PUBLIC_URL` | `https://angel-helper.ru` / `https://staging.angel-helper.ru` | проверка снаружи и ссылка в GitHub |
| `PRODUCTION_API_URL` | `https://angel-helper.ru/` | адрес сервера в release-сборке (только production) |

## Первый запуск — по порядку

1. Сервер(ы): `deploy/README.md`.
2. Репозиторий: `gh auth login && bash scripts/setup-github.sh`.
3. Секреты и переменные — таблицы выше.
4. Слить текущую работу: PR `social-features → develop` → выкладка
   на тестовый сервер.
5. Проверить на тестовом → PR `develop → main` → подтвердить выкладку.
6. Первый выпуск Android: `git tag v1.0.0 && git push origin v1.0.0`.

## Зависимости

Dependabot раз в неделю открывает PR в `develop` с обновлениями
Python, Gradle и GitHub Actions — каждый проходит те же проверки.
