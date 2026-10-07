package ru.angelhelper.app.push

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import ru.angelhelper.app.MainActivity
import ru.angelhelper.app.R

/**
 * Всё о push-уведомлениях, кроме приёма (он в PushService).
 */
object Push {

    const val EXTRA_CHAT_ID = "open_chat_id"
    private const val CHANNEL_MESSAGES = "messages"

    /**
     * Firebase настроен, только если сборка шла с google-services.json.
     * Без файла FirebaseApp не создаётся, и обращение к FirebaseMessaging
     * упало бы — поэтому каждый вход сюда начинается с этой проверки.
     */
    fun isAvailable(context: Context): Boolean = FirebaseApp.getApps(context).isNotEmpty()

    /** Токен этого телефона; onToken не вызывается, если Firebase нет или токен не выдан. */
    fun fetchToken(context: Context, onToken: (String) -> Unit) {
        if (!isAvailable(context)) return
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            if (!token.isNullOrBlank()) onToken(token)
        }
    }

    fun canNotify(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    private fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_MESSAGES) != null) return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_MESSAGES, "Сообщения", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Новые сообщения в чатах"
            }
        )
    }

    /** Уведомление о новом сообщении; нажатие открывает этот чат. */
    fun showMessage(context: Context, chatId: Int, title: String, text: String) {
        if (!canNotify(context)) return
        ensureChannel(context)
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_CHAT_ID, chatId)
        val pending = PendingIntent.getActivity(
            context, chatId, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_MESSAGES)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .build()
        try {
            // Номер уведомления — номер чата: новое сообщение заменяет
            // прежнее уведомление того же чата, а не копит их стопкой
            NotificationManagerCompat.from(context).notify(chatId, notification)
        } catch (_: SecurityException) {
            // разрешение отозвали между проверкой и показом — молчим
        }
    }

    fun cancel(context: Context, chatId: Int) {
        NotificationManagerCompat.from(context).cancel(chatId)
    }
}

/**
 * Какой чат сейчас открыт на экране. Уведомление о сообщении в этом
 * чате не показывается — человек его и так видит.
 */
object AppVisibility {
    @Volatile
    var openChatId: Int? = null
}

/**
 * Переход из уведомления: активность кладёт сюда номер чата, корневой
 * экран забирает его и открывает переписку. Через поток, а не прямым
 * вызовом модели: активность получает намерение раньше, чем появляется
 * модель, — при холодном запуске из уведомления.
 */
object PushRouter {
    private val _pendingChat = MutableStateFlow<Int?>(null)
    val pendingChat: StateFlow<Int?> = _pendingChat.asStateFlow()

    fun handle(intent: Intent?) {
        val id = intent?.getIntExtra(Push.EXTRA_CHAT_ID, -1) ?: -1
        if (id > 0) _pendingChat.value = id
    }

    fun consume() {
        _pendingChat.value = null
    }
}
