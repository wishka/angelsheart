package ru.angelhelper.app.data

import android.content.Context
import android.content.SharedPreferences

/**
 * Настройки приложения и токены доступа.
 *
 * SharedPreferences, а не DataStore: одна зависимость меньше, а
 * требований к скорости здесь нет — пишутся два десятка байт при входе.
 *
 * Токены хранятся зашифрованными ключом из AndroidKeyStore (TokenCipher):
 * в файле настроек лежит только шифротекст. Токены, сохранённые прежней
 * версией открытым текстом, при первом чтении перешифровываются.
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
        get() = readSecret(KEY_ACCESS)
        set(value) = writeSecret(KEY_ACCESS, value)

    var refreshToken: String?
        get() = readSecret(KEY_REFRESH)
        set(value) = writeSecret(KEY_REFRESH, value)

    /**
     * Чтение токена. Открытый текст от прежней версии приложения сразу
     * перешифровывается — иначе он так и лежал бы открытым до следующего
     * входа. Шифротекст, который не расшифровать, стирается: держать его
     * бессмысленно, а без токена человек увидит экран входа.
     */
    private fun readSecret(key: String): String? {
        val stored = prefs.getString(key, null) ?: return null
        if (!TokenCipher.isEncrypted(stored)) {
            writeSecret(key, stored)
            return stored
        }
        val plain = TokenCipher.decrypt(stored)
        if (plain == null) prefs.edit().remove(key).apply()
        return plain
    }

    private fun writeSecret(key: String, value: String?) {
        val sealed = value?.let(TokenCipher::encrypt)
        if (sealed == null) {
            prefs.edit().remove(key).apply()
        } else {
            prefs.edit().putString(key, sealed).apply()
        }
    }

    var username: String
        get() = prefs.getString(KEY_USERNAME, "") ?: ""
        set(value) = prefs.edit().putString(KEY_USERNAME, value).apply()

    /**
     * Последний токен push этого телефона. Нужен при выходе — сказать
     * серверу «больше сюда не слать» — и чтобы зарегистрировать токен,
     * выданный, пока человек не был вошедшим.
     */
    var pushToken: String
        get() = prefs.getString(KEY_PUSH_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PUSH_TOKEN, value).apply()

    /** Спрашивали ли уже разрешение на уведомления — чтобы не спрашивать при каждом входе. */
    var notificationsAsked: Boolean
        get() = prefs.getBoolean(KEY_NOTIFICATIONS_ASKED, false)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFICATIONS_ASKED, value).apply()

    // Демо-режим сам по себе входом не считается: иначе приложение при
    // следующем запуске открывало бы «Главную» мимо экрана входа сразу
    // после того, как переключатель вернул человека на этот экран.
    val signedIn: Boolean
        get() = !accessToken.isNullOrBlank()

    fun saveTokens(tokens: Tokens) {
        writeSecret(KEY_ACCESS, tokens.access)
        writeSecret(KEY_REFRESH, tokens.refresh)
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
        private const val KEY_PUSH_TOKEN = "push_token"
        private const val KEY_NOTIFICATIONS_ASKED = "notifications_asked"

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
