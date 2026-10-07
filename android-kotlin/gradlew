#!/bin/sh
#
# Запуск сборки через обёртку Gradle.
#
# Это укороченная версия стандартного gradlew. Всё, что делает
# настоящий скрипт сверх этого, — определяет JAVA_HOME десятком способов
# и переводит пути для cygwin. На macOS и Linux достаточно того, что
# ниже; Android Studio при первом открытии проекта всё равно заменит
# файл на свой канонический, и это нормально.
#
# SPDX-License-Identifier: Apache-2.0

set -e

APP_HOME=$(cd -P -- "$(dirname -- "$0")" && pwd -P)
WRAPPER_JAR="$APP_HOME/gradle/wrapper/gradle-wrapper.jar"

if [ ! -f "$WRAPPER_JAR" ]; then
    echo "Не найден $WRAPPER_JAR" >&2
    echo "Восстановить: gradle wrapper --gradle-version 8.9" >&2
    exit 1
fi

if [ -n "$JAVA_HOME" ] && [ -x "$JAVA_HOME/bin/java" ]; then
    JAVACMD="$JAVA_HOME/bin/java"
else
    JAVACMD=java
fi

if ! command -v "$JAVACMD" > /dev/null 2>&1; then
    echo "Java не найдена. Установите JDK 17 и задайте JAVA_HOME." >&2
    exit 1
fi

exec "$JAVACMD" \
    -Xmx64m -Xms64m \
    -classpath "$WRAPPER_JAR" \
    org.gradle.wrapper.GradleWrapperMain \
    "$@"
