package ru.angelhelper.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.runBlocking
import ru.angelhelper.app.data.AppPrefs
import ru.angelhelper.app.data.HttpApi
import ru.angelhelper.app.data.Outcome

/**
 * Приём push-уведомлений.
 *
 * В уведомлении от сервера только номер чата. Текст забирается здесь же
 * с нашего сервера, обычным запросом с токеном пользователя, и уже на
 * телефоне превращается в уведомление. Так через Google не проходит ни
 * имя отправителя, ни переписка.
 *
 * Запрашивается карточка чата, а не список сообщений: чтение сообщений
 * отмечает их прочитанными, и одно пришедшее уведомление гасило бы
 * счётчик непрочитанных раньше, чем человек что-то увидел.
 *
 * Методы сервиса Firebase вызывает не в главном потоке, и на работу
 * отводится около 10 секунд, поэтому синхронный запрос здесь уместен.
 */
class PushService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        val prefs = AppPrefs(this)
        prefs.pushToken = token
        if (!prefs.signedIn || prefs.demoMode) return
        runBlocking { HttpApi(prefs).registerDevice(token) }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val prefs = AppPrefs(this)
        if (!prefs.signedIn || prefs.demoMode) return
        if (message.data["type"] != "message") return
        val chatId = message.data["chat_id"]?.toIntOrNull() ?: return
        if (AppVisibility.openChatId == chatId) return

        val chat = runBlocking { HttpApi(prefs).chat(chatId) }
        if (chat is Outcome.Ok) {
            val info = chat.value
            val last = info.lastMessage
            // Скрытое и своё не показываем: уведомлять не о чем
            if (last == null || last.isMine || last.isHidden) return
            val text = if (info.isDirect) last.previewText else "${last.senderName}: ${last.previewText}"
            Push.showMessage(this, chatId, info.title, text)
        } else {
            // Сервер недоступен — сообщаем без подробностей, но сообщаем
            Push.showMessage(this, chatId, "Ангел-Хранитель", "Новое сообщение")
        }
    }
}
