package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.graphics.Color
import androidx.compose.runtime.remember
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Badge
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import ru.angelhelper.app.data.ChatInfo
import ru.angelhelper.app.data.ChatMessage
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.UiState

/** Как часто открытый чат спрашивает сервер о новых сообщениях. */
private const val POLL_INTERVAL_MS = 4_000L

// ==================== СПИСОК ЧАТОВ ====================

@Composable
fun ChatsScreen(vm: AppViewModel, state: UiState) {
    Panel {
        PrimaryButton("Новый чат", onClick = { vm.go(Screen.NewChat) })
    }

    Panel(title = "Переписки") {
        if (state.chats.isEmpty()) {
            EmptyNote(
                "Переписок пока нет. Найдите человека в разделе «Люди» или " +
                    "начните чат по логину кнопкой выше."
            )
        } else {
            state.chats.forEachIndexed { index, chat ->
                ChatRow(chat) { vm.go(Screen.Chat(chat.id)) }
                if (index != state.chats.lastIndex) HorizontalDivider()
            }
        }
        SecondaryButton("Обновить", onClick = { vm.loadChats() })
    }
}

@Composable
private fun ChatRow(chat: ChatInfo, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Initials(chat.title)
        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                chat.title,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = if (chat.unreadCount > 0) FontWeight.Bold else FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val last = chat.lastMessage
            val preview = when {
                last == null -> chat.kindTitle
                last.isMine -> "Вы: ${last.text}"
                chat.isDirect -> last.text
                else -> "${last.senderName}: ${last.text}"
            }
            Text(
                preview,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            chat.lastMessage?.createdAt?.let {
                Text(
                    it.asReadableDate(),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (chat.unreadCount > 0) Badge { Text("${chat.unreadCount}") }
        }
    }
}

// ==================== НОВЫЙ ЧАТ ====================

@Composable
fun NewChatScreen(vm: AppViewModel, state: UiState) {
    var recipients by rememberSaveable { mutableStateOf("") }
    var title by rememberSaveable { mutableStateOf("") }
    val names = recipients.split(',', ' ', '\n').map { it.trim().removePrefix("@") }.filter { it.isNotEmpty() }
    val isGroup = names.size > 1

    Panel(title = "Кому") {
        Field(
            recipients, { recipients = it }, "Логины через запятую",
            supporting = "Точный логин, как при переводе: написать можно и тому, кого нет в поиске",
        )
        if (isGroup) {
            Field(title, { title = it.take(80) }, "Название группового чата")
        }
        PrimaryButton(
            if (isGroup) "Создать групповой чат" else "Написать",
            busy = state.busy,
            enabled = names.isNotEmpty() && (!isGroup || title.isNotBlank()),
            onClick = { vm.createChat(names, if (isGroup) title.trim() else "") },
        )
        Caption("Один логин — личный чат. Несколько — групповой, ему нужно название.")
    }
}

// ==================== ПЕРЕПИСКА ====================

/**
 * Открытая переписка.
 *
 * Единственный экран, который не лежит в общей прокрутке AppRoot:
 * сообщения листаются своим списком, а поле ввода стоит внизу на месте.
 * Список перевёрнут (reverseLayout): новые сообщения сразу у поля ввода,
 * и при открытии не нужно прокручивать до конца.
 *
 * Пока экран открыт, раз в несколько секунд запрашиваются новые
 * сообщения. Цикл живёт в LaunchedEffect и останавливается сам, когда
 * человек уходит с экрана.
 */
@Composable
fun ChatScreen(vm: AppViewModel, state: UiState, id: Int) {
    var text by rememberSaveable(id) { mutableStateOf("") }
    var menuOpen by remember { mutableStateOf(false) }
    var confirmBlock by remember { mutableStateOf(false) }
    // Сообщение, на которое человек жалуется (долгое нажатие)
    var reporting by remember { mutableStateOf<ChatMessage?>(null) }
    val chat = state.openChat?.takeIf { it.id == id }

    LaunchedEffect(id) {
        while (true) {
            delay(POLL_INTERVAL_MS)
            vm.pollChat(id)
        }
    }

    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    chat?.title ?: "Загружаем…",
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (chat != null) Caption(chat.kindTitle)
            }
            if (chat != null) {
                Box {
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "Действия")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        ChatMenuItems(
                            chat = chat,
                            onTransfer = { username -> vm.transferTo(username) },
                            onBlock = { confirmBlock = true },
                            onUnblock = { userId -> vm.unblock(userId) },
                            onOpenGroup = { groupId -> vm.go(Screen.GroupDetail(groupId)) },
                            onLeave = { vm.leaveChat(id) },
                            close = { menuOpen = false },
                        )
                    }
                }
            }
        }

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            reverseLayout = true,
            verticalArrangement = Arrangement.spacedBy(6.dp),
            contentPadding = PaddingValues(vertical = 4.dp),
        ) {
            if (state.chatMessages.isEmpty()) {
                item { EmptyNote(if (chat == null) "Загружаем сообщения…" else "Сообщений пока нет — напишите первым.") }
            }
            items(state.chatMessages.asReversed(), key = { it.id }) { message ->
                Bubble(
                    message,
                    showSender = chat?.isDirect == false,
                    onLongPress = if (message.canReport) ({ reporting = message }) else null,
                )
            }
        }

        when (chat?.blockStatus) {
            "blocked_by_me" -> BlockedBar(
                "Вы заблокировали этого пользователя. Переписка закрыта.",
                action = "Разблокировать",
                onAction = { chat?.peerId?.let { vm.unblock(it) } },
            )
            "blocked_me" -> BlockedBar("Пользователь ограничил переписку с вами.")
            else -> MessageInput(
                text = text,
                onTextChange = { text = it.take(2000) },
                enabled = chat != null && !state.busy,
                onSend = {
                    vm.sendMessage(id, text.trim())
                    text = ""
                },
            )
        }
    }

    val peerUsername = chat?.peerUsername
    if (confirmBlock && chat != null && peerUsername != null) {
        ConfirmBlockDialog(
            name = chat.title,
            onConfirm = { vm.block(peerUsername) },
            onDismiss = { confirmBlock = false },
        )
    }

    reporting?.let { message ->
        ReportDialog(
            authorName = message.senderName,
            messageText = message.text,
            onSubmit = { reason, comment, alsoBlock ->
                vm.reportMessage(id, message.id, reason, comment, alsoBlock)
            },
            onDismiss = { reporting = null },
        )
    }
}

/** Пункты меню чата — свои для личного, группового чата и обсуждения группы. */
@Composable
private fun ChatMenuItems(
    chat: ChatInfo,
    onTransfer: (String) -> Unit,
    onBlock: () -> Unit,
    onUnblock: (Int) -> Unit,
    onOpenGroup: (Int) -> Unit,
    onLeave: () -> Unit,
    close: () -> Unit,
) {
    fun pick(action: () -> Unit): () -> Unit = {
        close()
        action()
    }
    val peerUsername = chat.peerUsername
    val peerId = chat.peerId
    val communityId = chat.communityId
    when (chat.kind) {
        "direct" -> {
            if (peerUsername != null && !chat.isBlocked) {
                DropdownMenuItem(text = { Text("Перевести деньги") }, onClick = pick { onTransfer(peerUsername) })
            }
            if (chat.blockStatus == "blocked_by_me" && peerId != null) {
                DropdownMenuItem(text = { Text("Разблокировать") }, onClick = pick { onUnblock(peerId) })
            } else if (peerUsername != null) {
                DropdownMenuItem(text = { Text("Заблокировать") }, onClick = pick(onBlock))
            }
        }
        "community" -> if (communityId != null) {
            DropdownMenuItem(text = { Text("Открыть группу") }, onClick = pick { onOpenGroup(communityId) })
        }
        else -> DropdownMenuItem(text = { Text("Выйти из чата") }, onClick = pick(onLeave))
    }
    DropdownMenuItem(
        text = { Text("Жалоба: удерживайте сообщение") },
        onClick = close,
        enabled = false,
    )
}

@Composable
private fun BlockedBar(text: String, action: String? = null, onAction: () -> Unit = {}) {
    Surface(
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            if (action != null) TextButton(onClick = onAction) { Text(action) }
        }
    }
}

@Composable
private fun MessageInput(text: String, onTextChange: (String) -> Unit, enabled: Boolean, onSend: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        OutlinedTextField(
            value = text,
            onValueChange = onTextChange,
            placeholder = { Text("Сообщение") },
            maxLines = 4,
            modifier = Modifier.weight(1f),
        )
        IconButton(enabled = enabled && text.isNotBlank(), onClick = onSend) {
            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Отправить")
        }
    }
}

/**
 * Пузырь сообщения.
 *
 * Долгое нажатие на чужое сообщение открывает жалобу. Скрытые сообщения
 * (модератором или из-за чёрного списка) рисуются приглушённо, и
 * пожаловаться на них нельзя — текста уже не видно.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Bubble(message: ChatMessage, showSender: Boolean, onLongPress: (() -> Unit)?) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (message.isMine) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = when {
                message.isHidden -> MaterialTheme.colorScheme.surfaceVariant
                message.isMine -> MaterialTheme.colorScheme.primary
                else -> MaterialTheme.colorScheme.surface
            },
            modifier = Modifier
                .widthIn(max = 300.dp)
                .then(
                    if (onLongPress != null) {
                        Modifier.combinedClickable(onClick = {}, onLongClick = onLongPress)
                    } else {
                        Modifier
                    }
                ),
        ) {
            Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                if (showSender && !message.isMine) {
                    Text(
                        message.senderName,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
                Text(
                    message.text,
                    style = MaterialTheme.typography.bodyLarge,
                    fontStyle = if (message.isHidden) FontStyle.Italic else FontStyle.Normal,
                    color = if (message.isHidden) MaterialTheme.colorScheme.onSurfaceVariant else Color.Unspecified,
                )
                Text(
                    message.createdAt.asReadableDate(),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (message.isMine && !message.isHidden) {
                        MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.75f)
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.align(Alignment.End),
                )
            }
        }
    }
}
