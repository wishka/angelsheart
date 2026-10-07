#!/usr/bin/env bash
#
# Откат на другой релиз без пересборки:
#
#   bash /srv/angelsheart/current/deploy/rollback.sh            # на предыдущий работавший
#   bash /srv/angelsheart/current/deploy/rollback.sh <RELEASE>  # на указанный
#
# Запускается вручную на сервере или из workflow «Rollback» в GitHub.
# Миграции НЕ откатываются: релизы обязаны быть совместимы со схемой
# базы следующего (docs/CICD.md). Если нужно вернуть и схему — дамп
# перед выкладкой лежит в backups/<RELEASE>.sql.gz.

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/srv/angelsheart}"
SERVICE="${SERVICE_NAME:-angelsheart}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health/}"
HEALTH_HOST="${HEALTH_HOST:-}"

CURRENT="$(basename "$(readlink -f "$APP_DIR/current")")"
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    # Предыдущий релиз, который действительно работал: последняя запись
    # журнала выкладок (deploy.sh пишет туда только успешные), отличная
    # от текущей и ещё не удалённая уборкой
    while read -r candidate; do
        if [ "$candidate" != "$CURRENT" ] && [ -d "$APP_DIR/releases/$candidate" ]; then
            TARGET="$candidate"
            break
        fi
    done < <(tac "$APP_DIR/history" 2>/dev/null || true)
fi
[ -n "$TARGET" ] || { echo "Откатываться не на что: других релизов нет"; exit 1; }
[ -d "$APP_DIR/releases/$TARGET" ] || { echo "Нет релиза $TARGET"; ls -1t "$APP_DIR/releases"; exit 1; }

echo "Откат: $CURRENT → $TARGET"
ln -sfn "releases/$TARGET" "$APP_DIR/current.tmp"
mv -Tf "$APP_DIR/current.tmp" "$APP_DIR/current"
sudo -n systemctl restart "$SERVICE"

args=(-fsS --max-time 5 -H "X-Forwarded-Proto: https")
[ -n "$HEALTH_HOST" ] && args+=(-H "Host: $HEALTH_HOST")
for _ in $(seq 1 20); do
    if curl "${args[@]}" "$HEALTH_URL" 2>/dev/null | grep -q "\"revision\": *\"$TARGET\""; then
        echo "Готово: работает $TARGET"
        exit 0
    fi
    sleep 3
done
echo "ВНИМАНИЕ: $TARGET не ответил на $HEALTH_URL — смотрите journalctl -u $SERVICE"
exit 1
