package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
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
import ru.angelhelper.app.data.Community
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.UiState

private fun statusTitle(status: String): String? = when (status) {
    "owner" -> "Вы владелец"
    "admin" -> "Вы администратор"
    "member" -> "Вы участник"
    "pending" -> "Заявка отправлена"
    else -> null
}

// ==================== СПИСОК ГРУПП ====================

@Composable
fun GroupsScreen(vm: AppViewModel, state: UiState) {
    var search by rememberSaveable { mutableStateOf("") }
    val mineOnly = state.groupsMineOnly

    CommunitySwitch(vm, peopleSelected = false)

    Panel(title = "Группы по интересам") {
        Field(search, { search = it }, "Название или описание")
        ChipChoice(
            listOf("all" to "Все группы", "mine" to "Мои"),
            isSelected = { (it == "mine") == mineOnly },
            onToggle = { vm.loadGroups(search.trim(), mineOnly = it == "mine") },
        )
        PrimaryButton("Найти", busy = state.busy, onClick = { vm.loadGroups(search.trim(), mineOnly) })
        SecondaryButton("Создать группу", onClick = { vm.go(Screen.NewGroup) })
    }

    if (state.groups.isEmpty()) {
        Panel {
            EmptyNote(if (mineOnly) "Вы пока не состоите в группах." else "Групп не нашлось.")
        }
    } else {
        state.groups.forEach { group ->
            GroupCard(group) { vm.go(Screen.GroupDetail(group.id)) }
        }
    }
}

@Composable
private fun GroupCard(group: Community, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Initials(group.name)
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(group.name, style = MaterialTheme.typography.titleMedium)
                Caption(group.summary)
                if (group.description.isNotBlank()) {
                    Text(
                        group.description.take(120) + if (group.description.length > 120) "…" else "",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                statusTitle(group.myStatus)?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

// ==================== КАРТОЧКА ГРУППЫ ====================

@Composable
fun GroupDetailScreen(vm: AppViewModel, state: UiState, id: Int) {
    val group = state.openGroup?.takeIf { it.id == id }
    if (group == null) {
        Panel { EmptyNote("Загружаем группу…") }
        return
    }

    Panel(title = group.name) {
        Caption(group.summary)
        if (group.description.isNotBlank()) Text(group.description, style = MaterialTheme.typography.bodyLarge)
        LabelValue("Владелец", "@${group.ownerUsername}")
        statusTitle(group.myStatus)?.let { LabelValue("Ваш статус", it, strong = true) }
    }

    Panel {
        val chatId = group.chatId
        when (group.myStatus) {
            "none" -> {
                PrimaryButton(
                    if (group.isPrivate) "Подать заявку" else "Вступить",
                    busy = state.busy,
                    onClick = { vm.groupAction(id, "join") },
                )
                if (group.isPrivate) Caption("Группа закрытая: заявку рассмотрит администратор.")
            }
            "pending" -> SecondaryButton("Отозвать заявку", onClick = { vm.groupAction(id, "leave") })
            else -> {
                if (chatId != null) {
                    PrimaryButton("Открыть обсуждение", onClick = { vm.go(Screen.Chat(chatId)) })
                }
                if (group.myStatus != "owner") {
                    SecondaryButton("Выйти из группы", onClick = { vm.groupAction(id, "leave") })
                }
            }
        }
    }

    if (group.canManage && group.pendingRequests.isNotEmpty()) {
        Panel(title = "Заявки (${group.pendingRequests.size})") {
            group.pendingRequests.forEachIndexed { index, request ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(request.displayName, fontWeight = FontWeight.SemiBold)
                        Caption("@${request.username}")
                    }
                    TextButton(onClick = { vm.groupAction(id, "decline", request.id) }) { Text("Отклонить") }
                    TextButton(onClick = { vm.groupAction(id, "approve", request.id) }) { Text("Принять") }
                }
                if (index != group.pendingRequests.lastIndex) HorizontalDivider()
            }
        }
    }

    Panel(title = "Участники") {
        if (group.members.isEmpty()) {
            Caption("Список участников и обсуждение видны только участникам группы.")
        } else {
            group.members.forEachIndexed { index, member ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { vm.go(Screen.PersonDetail(member.id)) }
                        .padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Initials(member.displayName, size = 36.dp)
                    Column(modifier = Modifier.weight(1f)) {
                        Text(member.displayName)
                        Caption("@${member.username}")
                    }
                    if (member.roleTitle.isNotEmpty()) Caption(member.roleTitle)
                }
                if (index != group.members.lastIndex) HorizontalDivider()
            }
        }
    }
}

// ==================== НОВАЯ ГРУППА ====================

@Composable
fun NewGroupScreen(vm: AppViewModel, state: UiState) {
    var name by rememberSaveable { mutableStateOf("") }
    var description by rememberSaveable { mutableStateOf("") }
    var topic by rememberSaveable { mutableStateOf("") }
    var isPrivate by rememberSaveable { mutableStateOf(false) }

    Panel(title = "Новая группа") {
        Field(name, { name = it.take(80) }, "Название")
        Field(description, { description = it.take(1000) }, "Описание", singleLine = false)
        Caption("Тема")
        ChipChoice(
            state.interests.map { it.slug to it.title },
            isSelected = { it == topic },
            onToggle = { slug -> topic = if (topic == slug) "" else slug },
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = isPrivate, onCheckedChange = { isPrivate = it })
            Text("Закрытая — вступление по заявке", style = MaterialTheme.typography.bodyMedium)
        }
        PrimaryButton(
            "Создать",
            busy = state.busy,
            enabled = name.isNotBlank(),
            onClick = { vm.createGroup(name.trim(), description.trim(), topic, isPrivate) },
        )
        Caption("Вы станете владельцем группы. У группы сразу появится своё обсуждение.")
    }
}
