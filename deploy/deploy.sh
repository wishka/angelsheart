#!/usr/bin/env bash
#
# Выкладка релиза на сервере. Запускается из GitHub Actions по SSH:
#
#   bash /srv/angelsheart/releases/<RELEASE>/deploy/deploy.sh <RELEASE>
#
# Код к этому моменту уже лежит в releases/<RELEASE> (его туда кладёт
# rsync из workflow). Схема каталогов:
#
#   $APP_DIR/
#     current -> releases/<RELEASE>   то, что сейчас обслуживает Gunicorn
#     releases/<RELEASE>/             код конкретного релиза
#     shared/                         то, что переживает релизы:
#       .env  media/  private-media/  logs/  app-release/
#     venvs/<hash requirements.txt>/  окружения Python, общие для релизов
#                                     с одинаковыми зависимостями
#     backups/                        дампы базы перед миграциями
#
# Порядок выбран так, чтобы сломанный релиз не дошёл до пользователей:
#   1. зависимости, check --deploy, бэкап, migrate, collectstatic — пока
#      работает прежний релиз; любая ошибка здесь останавливает выкладку,
#      и сайт этого не замечает;
#   2. атомарное переключение ссылки current и перезапуск Gunicorn;
#   3. проверка /health/: новый релиз должен ответить своим номером.
#      Не ответил — ссылка возвращается на прежний релиз.
#
# Миграции на шаге 1 применяются к базе, с которой ещё работает прежний
# код, — поэтому они обязаны быть обратно совместимыми (сначала добавить
# поле, убрать старое — следующим релизом). См. docs/CICD.md.

set -Eeuo pipefail

RELEASE="${1:?Использование: deploy.sh <RELEASE>}"
APP_DIR="${APP_DIR:-/srv/angelsheart}"
SERVICE="${SERVICE_NAME:-angelsheart}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health/}"
HEALTH_HOST="${HEALTH_HOST:-}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
BACKUP_DB="${BACKUP_DB:-1}"
PYTHON="${PYTHON:-python3}"

RELEASE_DIR="$APP_DIR/releases/$RELEASE"
SHARED="$APP_DIR/shared"

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "ОШИБКА: $*"; exit 1; }

[ -d "$RELEASE_DIR" ] || fail "нет каталога релиза $RELEASE_DIR"
[ -f "$SHARED/.env" ] || fail "нет $SHARED/.env — сервер не подготовлен (deploy/README.md)"

PREVIOUS=""
if [ -L "$APP_DIR/current" ]; then
    PREVIOUS="$(basename "$(readlink -f "$APP_DIR/current")")"
fi
log "релиз $RELEASE, сейчас работает: ${PREVIOUS:-ничего}"

# ---------- 1. Подготовка, пока работает прежний релиз ----------

cd "$RELEASE_DIR"
echo "$RELEASE" > REVISION

# Общие файлы и каталоги — ссылками в релиз: код ищет их от BASE_DIR
mkdir -p "$SHARED"/{media,private-media,logs,app-release} "$APP_DIR/venvs" "$APP_DIR/backups"
for item in .env media private-media logs app-release; do
    rm -rf "./$item"
    ln -s "$SHARED/$item" "./$item"
done

# Окружение Python заново ставится, только если изменились зависимости
REQ_HASH="$(sha256sum requirements.txt | cut -c1-16)"
VENV="$APP_DIR/venvs/$REQ_HASH"
if [ ! -x "$VENV/bin/python" ]; then
    log "новые зависимости — создаю окружение $REQ_HASH"
    rm -rf "$VENV.tmp"
    "$PYTHON" -m venv "$VENV.tmp"
    "$VENV.tmp/bin/pip" install --quiet --upgrade pip
    "$VENV.tmp/bin/pip" install --quiet -r requirements.txt
    mv "$VENV.tmp" "$VENV"
fi
ln -sfn "$VENV" .venv
PY="$RELEASE_DIR/.venv/bin/python"

# manage.py читает .env сам. Для pg_dump нужны только параметры базы —
# они берутся тем же разборщиком (python-dotenv), а не source .env:
# значения вроде «ИП [ФАМИЛИЯ И.О.]» с пробелами оболочка разобрала бы
# как команды
env_value() {
    "$PY" -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or "")' \
        "$SHARED/.env" "$1"
}

log "manage.py check --deploy"
"$PY" manage.py check --deploy

DB_NAME="$(env_value POSTGRES_DB)"
if [ "$BACKUP_DB" = "1" ] && [ -n "$DB_NAME" ] && command -v pg_dump >/dev/null; then
    DUMP="$APP_DIR/backups/${RELEASE}.sql.gz"
    log "резервная копия базы → $DUMP"
    PGPASSWORD="$(env_value POSTGRES_PASSWORD)" pg_dump \
        -h "$(env_value POSTGRES_HOST | sed 's/^$/localhost/')" \
        -p "$(env_value POSTGRES_PORT | sed 's/^$/5432/')" \
        -U "$(env_value POSTGRES_USER | sed 's/^$/postgres/')" "$DB_NAME" | gzip > "$DUMP"
    # Храним 10 последних дампов
    ls -1t "$APP_DIR"/backups/*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

log "migrate"
"$PY" manage.py migrate --noinput
log "collectstatic"
"$PY" manage.py collectstatic --noinput --verbosity 0

# ---------- 2. Переключение ----------

switch_to() {
    ln -sfn "releases/$1" "$APP_DIR/current.tmp"
    mv -Tf "$APP_DIR/current.tmp" "$APP_DIR/current"
    sudo -n systemctl restart "$SERVICE"
}

log "переключаю current → $RELEASE и перезапускаю $SERVICE"
switch_to "$RELEASE"

# ---------- 3. Проверка ----------

healthy() {
    local args=(-fsS --max-time 5 -H "X-Forwarded-Proto: https")
    [ -n "$HEALTH_HOST" ] && args+=(-H "Host: $HEALTH_HOST")
    curl "${args[@]}" "$HEALTH_URL" 2>/dev/null | grep -q "\"revision\": *\"$1\""
}

wait_healthy() {  # релиз, число попыток по 3 секунды
    for _ in $(seq 1 "$2"); do
        healthy "$1" && return 0
        sleep 3
    done
    return 1
}

if wait_healthy "$RELEASE" 20; then
    log "релиз $RELEASE отвечает"
else
    if [ -n "$PREVIOUS" ]; then
        log "релиз не ответил — возвращаю $PREVIOUS"
        switch_to "$PREVIOUS"
        if wait_healthy "$PREVIOUS" 20; then
            log "прежний релиз $PREVIOUS снова работает"
        else
            log "ВНИМАНИЕ: и прежний релиз $PREVIOUS не отвечает — сайт лежит, нужен человек"
        fi
    fi
    fail "проверка $HEALTH_URL не прошла за 60 секунд; журнал: journalctl -u $SERVICE"
fi

# Журнал выкладок: по нему rollback.sh находит предыдущий РАБОТАВШИЙ
# релиз. Брать «предыдущий каталог по времени» нельзя — им может
# оказаться выкладка, которая упала и в работу так и не вышла.
echo "$RELEASE" >> "$APP_DIR/history"
tail -n 50 "$APP_DIR/history" > "$APP_DIR/history.tmp" && mv -f "$APP_DIR/history.tmp" "$APP_DIR/history"

# ---------- Уборка ----------

cd "$APP_DIR/releases"
# shellcheck disable=SC2012  # имена релизов — дата-время-коммит, без пробелов
ls -1t | tail -n +"$((KEEP_RELEASES + 1))" | while read -r old; do
    [ "$old" = "$RELEASE" ] && continue
    log "удаляю старый релиз $old"
    rm -rf "$old"
done

# Окружения, на которые не ссылается ни один оставшийся релиз
for venv in "$APP_DIR"/venvs/*; do
    [ -d "$venv" ] || continue
    if ! find "$APP_DIR/releases" -maxdepth 2 -name .venv -lname "$venv" | grep -q .; then
        log "удаляю неиспользуемое окружение $(basename "$venv")"
        rm -rf "$venv"
    fi
done

log "готово: $RELEASE"
