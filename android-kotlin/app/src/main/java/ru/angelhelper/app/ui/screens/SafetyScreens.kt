package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.REPORT_REASONS
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.UiState

/**
 * Безопасность общения: чёрный список, подтверждение блокировки,
 * жалоба на сообщение.
 */

// ==================== ЧЁРНЫЙ СПИСОК ====================

@Composable
fun BlockListScreen(vm: AppViewModel, state: UiState) {
    Panel(title = "Чёрный список") {
        Caption(
            "Те, кого вы заблокировали, не могут писать вам в личные сообщения " +
                "и не видят вас в поиске; вы тоже не видите их. В общих чатах их " +
                "сообщения скрыты. О блокировке человеку не сообщается."
        )
        if (state.blocks.isEmpty()) {
            EmptyNote("Список пуст.")
        } else {
            state.blocks.forEachIndexed { index, blocked ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Initials(blocked.displayName, size = 36.dp)
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .padding(horizontal = 12.dp),
                    ) {
                        Text(blocked.displayName, fontWeight = FontWeight.SemiBold)
                        Caption("@${blocked.username} · с ${blocked.createdAt.asReadableDate()}")
                    }
                    TextButton(onClick = { vm.unblock(blocked.id) }) { Text("Разблокировать") }
                }
                if (index != state.blocks.lastIndex) HorizontalDivider()
            }
        }
        SecondaryButton("Обновить", onClick = { vm.loadBlocks() })
    }
}

// ==================== ДИАЛОГИ ====================

/**
 * Подтверждение блокировки.
 *
 * Отдельным окном, а не сразу по нажатию: блокировка закрывает
 * переписку в обе стороны, и случайное касание пункта меню не должно
 * обрывать разговор без предупреждения.
 */
@Composable
fun ConfirmBlockDialog(name: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Заблокировать $name?") },
        text = {
            Text(
                "Вы не сможете переписываться в личных сообщениях и не будете " +
                    "видеть друг друга в поиске. В общих чатах сообщения $name " +
                    "будут скрыты. Разблокировать можно в Профиле → Чёрный список."
            )
        },
        confirmButton = {
            TextButton(onClick = {
                onConfirm()
                onDismiss()
            }) { Text("Заблокировать") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

/**
 * Жалоба на сообщение.
 *
 * Причина обязательна: модератору нужно понимать, что искать в
 * сообщении, а «просто не понравилось» жалобой не является. Галочка
 * «заблокировать автора» стоит здесь же — чаще всего человек, который
 * жалуется, больше не хочет получать сообщения от этого автора.
 */
@Composable
fun ReportDialog(
    authorName: String,
    messageText: String,
    onSubmit: (reason: String, comment: String, alsoBlock: Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    var reason by rememberSaveable { mutableStateOf("") }
    var comment by rememberSaveable { mutableStateOf("") }
    var alsoBlock by rememberSaveable { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Жалоба на сообщение") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "$authorName: «${messageText.take(120)}${if (messageText.length > 120) "…" else ""}»",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                REPORT_REASONS.forEach { (key, title) ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { reason = key },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = reason == key, onClick = { reason = key })
                        Text(title)
                    }
                }
                OutlinedTextField(
                    value = comment,
                    onValueChange = { comment = it.take(500) },
                    label = { Text("Комментарий (необязательно)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = alsoBlock, onCheckedChange = { alsoBlock = it })
                    Text("Заблокировать $authorName")
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = reason.isNotEmpty(),
                onClick = {
                    onSubmit(reason, comment.trim(), alsoBlock)
                    onDismiss()
                },
            ) { Text("Отправить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
