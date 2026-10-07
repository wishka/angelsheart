package ru.angelhelper.app.data

import android.content.Context
import android.content.SharedPreferences

/**
 * Настройки приложения и токены доступа.
 *
 * SharedPreferences, а не DataStore: одна зависимость меньше, а
 * требований к скорости здесь нет — пишутся два десятка байт при входе.
 *
 * Токены лежат в обычном файле настроек и НЕ зашифрованы. Для тестового
 * приложения это допустимо и сказано вслух; для боевого понадобится
 * EncryptedSharedPreferences или хранилище ключей Android, иначе токен
 * читается с устройства с разблокированным загрузчиком.
 */
class AppPrefs(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("angelsheart", Context.MODE_PRIVATE)

    /**
     * Адрес сервера.
     *
     * По умолчанию — именно 127.0.0.1, как локальный сервер и называется.
     * Важная тонкость: на телефоне «localhost» — это сам телефон, а не
     * ноутбук. Чтобы адрес заработал, нужен `adb reverse tcp:8000
     * tcp:8000` — тогда телефон пробрасывает этот порт на машину, где
     * запущен Django. В эмуляторе хозяйская машина видна как 10.0.2.2.
     * Оба варианта вынесены на экран настроек кнопками.
     */
    var baseUrl: String
        get() = prefs.getString(KEY_BASE_URL, DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(value) = prefs.edit().putString(KEY_BASE_URL, normalizeUrl(value)).apply()

    /** Демо-режим: данные берутся из памяти, сеть не трогается вовсе. */
    var demoMode: Boolean
        get() = prefs.getBoolean(KEY_DEMO, false)
        set(value) = prefs.edit().putBoolean(KEY_DEMO, value).apply()

    var accessToken: String?
        get() = prefs.getString(KEY_ACCESS, null)
        set(value) = prefs.edit().putString(KEY_ACCESS, value).apply()

    var refreshToken: String?
        get() = prefs.getString(KEY_REFRESH, null)
        set(value) = prefs.edit().putString(KEY_REFRESH, value).apply()

    var username: String
        get() = prefs.getString(KEY_USERNAME, "") ?: ""
        set(value) = prefs.edit().putString(KEY_USERNAME, value).apply()

    // Демо-режим сам по себе входом не считается: иначе приложение при
    // следующем запуске открывало бы «Главную» мимо экрана входа сразу
    // после того, как переключатель вернул человека на этот экран.
    val signedIn: Boolean
        get() = !accessToken.isNullOrBlank()

    fun saveTokens(tokens: Tokens) {
        prefs.edit()
            .putString(KEY_ACCESS, tokens.access)
            .putString(KEY_REFRESH, tokens.refresh)
            .apply()
    }

    fun clearTokens() {
        prefs.edit().remove(KEY_ACCESS).remove(KEY_REFRESH).remove(KEY_USERNAME).apply()
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://127.0.0.1:8000/"
        const val EMULATOR_BASE_URL = "http://10.0.2.2:8000/"

        private const val KEY_BASE_URL = "base_url"
        private const val KEY_DEMO = "demo_mode"
        private const val KEY_ACCESS = "access"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_USERNAME = "username"

        /**
         * Приводит адрес к виду, от которого потом строятся все пути.
         *
         * Без завершающей косой черты «http://host:8000» + «api/...»
         * склеивалось бы в «http://host:8000api/...». Отдельная функция,
         * чтобы это не зависело от того, что человек набрал в настройках.
         */
        fun normalizeUrl(raw: String): String {
            var value = raw.trim()
            if (value.isEmpty()) return DEFAULT_BASE_URL
            if (!value.startsWith("http://") && !value.startsWith("https://")) {
                value = "http://$value"
            }
            if (!value.endsWith("/")) value = "$value/"
            return value
        }
    }
}
