package ru.angelhelper.app.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * Фото без сторонних библиотек.
 *
 * Coil или Glide сделали бы то же короче, но это ещё одна зависимость
 * с собственными версиями, а приложение сознательно держится платформы.
 * Здесь нужно немногое: уменьшить снимок перед отправкой, раскодировать
 * присланный и держать недавние в памяти.
 */
object ImageTools {

    /**
     * Снимок из галереи → JPEG не больше maxSide по длинной стороне.
     *
     * Уменьшение на телефоне экономит трафик: снимок камеры весит 4–8 МБ,
     * а сервер всё равно ужмёт его до 1600 точек. Поворот по EXIF
     * применяется здесь же: пересохранённый JPEG метаданных уже не несёт,
     * и без поворота фото «с боку» так бы и осталось лежать на боку.
     */
    fun prepareJpeg(context: Context, uri: Uri, maxSide: Int): ByteArray? = try {
        val resolver = context.contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        val longest = maxOf(bounds.outWidth, bounds.outHeight)
        if (longest <= 0) {
            null
        } else {
            var sample = 1
            while (longest / (sample * 2) >= maxSide) sample *= 2
            val options = BitmapFactory.Options().apply { inSampleSize = sample }
            val decoded = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
            val rotation = resolver.openInputStream(uri)?.use { stream ->
                when (ExifInterface(stream).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL,
                )) {
                    ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                    ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                    ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                    else -> 0f
                }
            } ?: 0f
            decoded?.let { encode(scaleAndRotate(it, maxSide, rotation)) }
        }
    } catch (_: Exception) {
        null
    }

    private fun scaleAndRotate(source: Bitmap, maxSide: Int, rotation: Float): Bitmap {
        val longest = maxOf(source.width, source.height)
        val scale = if (longest > maxSide) maxSide.toFloat() / longest else 1f
        if (scale == 1f && rotation == 0f) return source
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(rotation)
        }
        return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    }

    private fun encode(bitmap: Bitmap): ByteArray {
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 85, out)
        return out.toByteArray()
    }

    fun decode(bytes: ByteArray): ImageBitmap? =
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
}

/**
 * Недавно показанные фото. По числу, а не по байтам: фото анкеты
 * маленькие (512 точек), фото из чата — до 1600, и 40 штук даже худшего
 * случая укладываются в память любого телефона из поддерживаемых.
 */
object ImageCache {
    private val cache = LruCache<String, ImageBitmap>(40)

    fun get(key: String): ImageBitmap? = cache.get(key)

    fun put(key: String, image: ImageBitmap) {
        cache.put(key, image)
    }
}

/**
 * Изображение с сервера. key — ключ кэша (включает версию фото, чтобы
 * после смены не показывать старое); load — загрузка байтов, null при
 * любой неудаче: вместо фото останется подложка, без всплывающих ошибок.
 */
@Composable
fun RemoteImage(
    key: String,
    load: suspend () -> ByteArray?,
    modifier: Modifier = Modifier,
    contentScale: ContentScale = ContentScale.Crop,
    placeholder: @Composable () -> Unit = {
        Box(modifier.background(MaterialTheme.colorScheme.surfaceVariant))
    },
) {
    val image by produceState(initialValue = ImageCache.get(key), key) {
        if (value == null) {
            value = load()?.let { bytes -> withContext(Dispatchers.Default) { ImageTools.decode(bytes) } }
                ?.also { ImageCache.put(key, it) }
        }
    }
    val loaded = image
    if (loaded != null) {
        Image(bitmap = loaded, contentDescription = null, modifier = modifier, contentScale = contentScale)
    } else {
        placeholder()
    }
}
