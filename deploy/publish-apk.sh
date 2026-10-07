#!/usr/bin/env bash
#
# Публикация APK для скачивания с сайта. Вызывается из workflow «Release»:
#
#   bash publish-apk.sh <путь к загруженному APK> <версия>
#
# Файл кладётся в shared/app-release/angelsheart.apk — туда смотрит
# ANDROID_APK_PATH по умолчанию (BASE_DIR/app-release ссылкой ведёт в
# shared). Замена атомарная: скачивание, начатое до публикации, не
# получит половину старого файла и половину нового.

set -Eeuo pipefail

UPLOADED="${1:?Использование: publish-apk.sh <apk> <версия>}"
VERSION="${2:?Использование: publish-apk.sh <apk> <версия>}"
APP_DIR="${APP_DIR:-/srv/angelsheart}"
TARGET_DIR="$APP_DIR/shared/app-release"

mkdir -p "$TARGET_DIR/archive"
cp "$UPLOADED" "$TARGET_DIR/archive/angelsheart-$VERSION.apk"
cp "$UPLOADED" "$TARGET_DIR/angelsheart.apk.tmp"
mv -f "$TARGET_DIR/angelsheart.apk.tmp" "$TARGET_DIR/angelsheart.apk"
echo "$VERSION" > "$TARGET_DIR/VERSION"
rm -f "$UPLOADED"

# Архив: 5 последних версий — на случай, если новую придётся отозвать
ls -1t "$TARGET_DIR"/archive/*.apk | tail -n +6 | xargs -r rm -f
echo "Опубликован APK $VERSION: $TARGET_DIR/angelsheart.apk"
