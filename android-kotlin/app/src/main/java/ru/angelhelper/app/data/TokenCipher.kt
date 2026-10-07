package ru.angelhelper.app.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Шифрование токенов ключом из хранилища ключей Android.
 *
 * Ключ AES-256 создаётся в AndroidKeyStore и не покидает его: прочитать
 * его нельзя даже с разблокированным загрузчиком, на многих телефонах он
 * лежит в аппаратном модуле. В настройках остаётся только шифротекст.
 *
 * Не EncryptedSharedPreferences: библиотека androidx.security-crypto
 * объявлена устаревшей, а нужное здесь — десяток строк поверх платформы
 * и ни одной лишней зависимости, как и во всём приложении.
 *
 * Формат строки: «v1:<iv>:<шифротекст>», оба в Base64. Метка версии —
 * чтобы однажды сменить схему, не ломая уже сохранённые токены.
 */
object TokenCipher {

    private const val KEYSTORE = "AndroidKeyStore"
    private const val KEY_ALIAS = "angelsheart_tokens_v1"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val PREFIX = "v1:"
    private const val TAG_BITS = 128

    fun isEncrypted(stored: String): Boolean = stored.startsWith(PREFIX)

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    /** null — шифрование недоступно; токен тогда не сохраняется вовсе, а не пишется открытым. */
    fun encrypt(plain: String): String? = try {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        PREFIX + encode(cipher.iv) + ":" + encode(sealed)
    } catch (_: Exception) {
        null
    }

    /**
     * null — расшифровать нельзя. Так бывает, если ключ пропал (сброс
     * блокировки экрана на части устройств удаляет ключи) — тогда
     * человеку просто придётся войти заново.
     */
    fun decrypt(stored: String): String? {
        if (!isEncrypted(stored)) return null
        val parts = stored.removePrefix(PREFIX).split(":")
        if (parts.size != 2) return null
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, decode(parts[0])))
            String(cipher.doFinal(decode(parts[1])), Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    private fun encode(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)

    private fun decode(text: String): ByteArray = Base64.decode(text, Base64.NO_WRAP)
}
