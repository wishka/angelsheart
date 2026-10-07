package ru.angelhelper.app.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.Post
import ru.angelhelper.app.data.PostComment
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.RemoteImage
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.UiState

/**
 * Лента — первый экран после входа.
 *
 * Две вкладки: «Подписки» (свои записи и тех, на кого подписан) и «Все»
 * (публичные записи людей с открытой анкетой). Подгрузка — кнопкой
 * «Показать ещё», а не бесконечной прокруткой: весь экран лежит в общей
 * прокрутке, и ленивый список внутри неё невозможен.
 */
@Composable
fun FeedScreen(vm: AppViewModel, state: UiState) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(
            selected = state.feedScope == "following",
            onClick = { vm.loadFeed("following") },
            label = { Text("Подписки") },
        )
        FilterChip(
            selected = state.feedScope == "all",
            onClick = { vm.loadFeed("all") },
            label = { Text("Все") },
        )
    }

    PrimaryButton("Новая запись", onClick = { vm.go(Screen.NewPost) })

    if (state.feed.isEmpty()) {
        Panel {
            EmptyNote(
                if (state.feedScope == "following") {
                    "Здесь будут ваши записи и записи тех, на кого вы подписаны. " +
                        "Найдите людей во вкладке «Люди» или загляните во «Все»."
                } else {
                    "Публичных записей пока нет. Станьте первым!"
                }
            )
        }
    } else {
        state.feed.forEach { post ->
            PostCard(vm, post, onOpen = { vm.go(Screen.PostDetail(post.id)) })
        }
        if (state.feedNext != null) {
            SecondaryButton("Показать ещё", onClick = { vm.moreFeed() })
        }
    }
}

/**
 * Карточка публикации: автор, текст, фото, «нравится» и комментарии.
 *
 * onOpen == null — карточка уже открыта на своём экране, и нажатие на
 * неё никуда не ведёт.
 */
@Composable
fun PostCard(vm: AppViewModel, post: Post, onOpen: (() -> Unit)?) {
    var menuOpen by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var reporting by remember { mutableStateOf(false) }

    Panel {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(modifier = Modifier.clickable { vm.go(Screen.PersonDetail(post.author.id)) }) {
                PersonAvatar(
                    vm, post.author.id, post.author.displayName,
                    post.author.hasAvatar, post.author.avatarVersion, size = 40.dp,
                )
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .clickable { vm.go(Screen.PersonDetail(post.author.id)) },
            ) {
                Text(post.author.displayName, fontWeight = FontWeight.SemiBold)
                Caption(
                    listOfNotNull(
                        post.createdAt.asReadableDate(),
                        post.visibilityTitle.takeIf { post.isMine },
                        "изменено".takeIf { post.edited },
                    ).joinToString(" · ")
                )
            }
            Box {
                IconButton(onClick = { menuOpen = true }) {
                    Icon(Icons.Default.MoreVert, contentDescription = "Действия")
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    if (post.isMine) {
                        DropdownMenuItem(text = { Text("Изменить") }, onClick = {
                            menuOpen = false
                            editing = true
                        })
                        DropdownMenuItem(text = { Text("Удалить") }, onClick = {
                            menuOpen = false
                            confirmDelete = true
                        })
                    } else {
                        DropdownMenuItem(text = { Text("Пожаловаться") }, onClick = {
                            menuOpen = false
                            reporting = true
                        })
                    }
                }
            }
        }

        if (post.isHidden) {
            Caption("Запись скрыта после жалоб и ждёт проверки модератора. Её видите только вы.")
        }

        if (post.text.isNotBlank()) {
            Text(
                post.text,
                style = MaterialTheme.typography.bodyLarge,
                modifier = if (onOpen != null) Modifier.clickable(onClick = onOpen) else Modifier,
            )
        }

        if (post.hasImage) {
            RemoteImage(
                key = "post:${post.id}",
                load = { vm.postImageBytes(post.id) },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(260.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .then(if (onOpen != null) Modifier.clickable(onClick = onOpen) else Modifier),
            )
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { vm.toggleLike(post) }) {
                Icon(
                    if (post.liked) Icons.Default.Favorite else Icons.Default.FavoriteBorder,
                    contentDescription = if (post.liked) "Убрать отметку" else "Нравится",
                    tint = if (post.liked) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text("${post.likesCount}")
            TextButton(enabled = onOpen != null, onClick = { onOpen?.invoke() }) {
                Text("Комментарии: ${post.commentsCount}")
            }
        }
    }

    if (editing) {
        EditTextDialog(
            title = "Изменить запись",
            initial = post.text,
            maxLength = 5000,
            onSave = { vm.editPost(post.id, it) },
            onDismiss = { editing = false },
        )
    }
    if (confirmDelete) {
        ConfirmDialog(
            title = "Удалить запись?",
            text = "Запись исчезнет вместе с комментариями и отметками. Отменить это нельзя.",
            confirm = "Удалить",
            onConfirm = { vm.deletePost(post.id) },
            onDismiss = { confirmDelete = false },
        )
    }
    if (reporting) {
        ReportDialog(
            authorName = post.author.displayName,
            messageText = post.text,
            title = "Жалоба на запись",
            onSubmit = { reason, comment, alsoBlock -> vm.reportPost(post.id, null, reason, comment, alsoBlock) },
            onDismiss = { reporting = false },
        )
    }
}

// ==================== ЗАПИСЬ И КОММЕНТАРИИ ====================

@Composable
fun PostDetailScreen(vm: AppViewModel, state: UiState, id: Long) {
    val post = state.openPost?.takeIf { it.id == id }
    if (post == null) {
        Panel { EmptyNote("Загружаем запись…") }
        return
    }
    PostCard(vm, post, onOpen = null)

    var text by rememberSaveable(id) { mutableStateOf("") }
    Panel(title = "Комментарии") {
        Field(text, { text = it.take(1000) }, "Ваш комментарий", singleLine = false)
        PrimaryButton(
            "Отправить",
            busy = state.busy,
            enabled = text.isNotBlank(),
            onClick = {
                vm.addComment(id, text.trim())
                text = ""
            },
        )
        if (state.comments.isEmpty()) {
            EmptyNote("Комментариев пока нет.")
        } else {
            state.comments.forEachIndexed { index, comment ->
                CommentRow(vm, id, comment)
                if (index != state.comments.lastIndex) HorizontalDivider()
            }
        }
        if (state.commentsNext != null) {
            SecondaryButton("Показать ещё", onClick = { vm.moreComments(id) })
        }
    }
}

@Composable
private fun CommentRow(vm: AppViewModel, postId: Long, comment: PostComment) {
    var reporting by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                comment.author.displayName,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier
                    .weight(1f)
                    .clickable { vm.go(Screen.PersonDetail(comment.author.id)) },
            )
            Caption(comment.createdAt.asReadableDate())
        }
        Text(comment.text, style = MaterialTheme.typography.bodyLarge)
        Row {
            if (comment.canDelete) {
                TextButton(onClick = { confirmDelete = true }) { Text("Удалить") }
            }
            if (!comment.isMine) {
                TextButton(onClick = { reporting = true }) { Text("Пожаловаться") }
            }
        }
    }
    if (confirmDelete) {
        ConfirmDialog(
            title = "Удалить комментарий?",
            text = "«${comment.text.take(80)}»",
            confirm = "Удалить",
            onConfirm = { vm.deleteComment(postId, comment.id) },
            onDismiss = { confirmDelete = false },
        )
    }
    if (reporting) {
        ReportDialog(
            authorName = comment.author.displayName,
            messageText = comment.text,
            title = "Жалоба на комментарий",
            onSubmit = { reason, text, alsoBlock -> vm.reportPost(postId, comment.id, reason, text, alsoBlock) },
            onDismiss = { reporting = false },
        )
    }
}

// ==================== НОВАЯ ЗАПИСЬ ====================

/**
 * Новая запись.
 *
 * «Всем» — запись увидят все пользователи во вкладке «Все». Это
 * распространение персональных данных, поэтому сервер пропускает его
 * только при открытой анкете (согласие по ст. 10.1 152-ФЗ); иначе он
 * сам сузит видимость до подписчиков и объяснит почему.
 */
@Composable
fun NewPostScreen(vm: AppViewModel, state: UiState) {
    var text by rememberSaveable { mutableStateOf("") }
    var visibility by rememberSaveable { mutableStateOf("followers") }
    var photo by remember { mutableStateOf<Uri?>(null) }
    val pickPhoto = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) photo = uri
    }

    Panel(title = "Новая запись") {
        Field(text, { text = it.take(5000) }, "Что у вас нового?", singleLine = false)
        if (photo == null) {
            SecondaryButton("Добавить фото", onClick = {
                pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            })
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Фото выбрано", modifier = Modifier.weight(1f))
                TextButton(onClick = { photo = null }) { Text("Убрать") }
            }
        }
        Caption("Кто увидит")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = visibility == "followers",
                onClick = { visibility = "followers" },
                label = { Text("Подписчики") },
            )
            FilterChip(
                selected = visibility == "public",
                onClick = { visibility = "public" },
                label = { Text("Все") },
            )
        }
        if (visibility == "public") {
            Caption(
                "Запись для всех доступна при открытой анкете (Профиль → Анкета в " +
                    "сообществе → «Показывать в поиске»). Без неё запись увидят только подписчики."
            )
        }
        PrimaryButton(
            "Опубликовать",
            busy = state.busy,
            enabled = text.isNotBlank() || photo != null,
            onClick = { vm.createPost(text.trim(), photo, visibility) },
        )
    }
}

// ==================== ОБЩИЕ ДИАЛОГИ ====================

@Composable
fun EditTextDialog(
    title: String,
    initial: String,
    maxLength: Int,
    onSave: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var value by rememberSaveable { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            OutlinedTextField(
                value = value,
                onValueChange = { value = it.take(maxLength) },
                minLines = 3,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            TextButton(
                enabled = value.isNotBlank() && value != initial,
                onClick = {
                    onSave(value.trim())
                    onDismiss()
                },
            ) { Text("Сохранить") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
fun ConfirmDialog(
    title: String,
    text: String,
    confirm: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = {
            TextButton(onClick = {
                onConfirm()
                onDismiss()
            }) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
