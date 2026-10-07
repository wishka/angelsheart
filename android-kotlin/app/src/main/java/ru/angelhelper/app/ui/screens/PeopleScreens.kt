package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.GENDERS
import ru.angelhelper.app.data.Interest
import ru.angelhelper.app.data.PeopleFilter
import ru.angelhelper.app.data.Person
import ru.angelhelper.app.data.SocialProfile
import ru.angelhelper.app.data.ageTitle
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.StubBanner
import ru.angelhelper.app.ui.UiState

// ==================== ОБЩИЕ ЧАСТИ СООБЩЕСТВА ====================

/** Кружок с инициалами вместо фотографии: фото в анкете пока нет. */
@Composable
fun Initials(name: String, size: Dp = 44.dp) {
    val letters = name.split(' ', '_', '.')
        .filter { it.isNotBlank() }
        .take(2)
        .joinToString("") { it.first().uppercase() }
        .ifEmpty { "?" }
    Box(
        modifier = Modifier
            .size(size)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.primaryContainer),
        contentAlignment = Alignment.Center,
    ) {
        Text(letters, color = MaterialTheme.colorScheme.onPrimaryContainer, fontWeight = FontWeight.Bold)
    }
}

/**
 * Переключатель «Люди | Группы» сверху раздела сообщества.
 *
 * Одна вкладка нижней панели на оба экрана: панель и так держит пять
 * разделов, шестой сжал бы подписи до нечитаемых.
 */
@Composable
fun CommunitySwitch(vm: AppViewModel, peopleSelected: Boolean) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(
            selected = peopleSelected,
            onClick = { if (!peopleSelected) vm.goRoot(Screen.People) },
            label = { Text("Люди") },
        )
        FilterChip(
            selected = !peopleSelected,
            onClick = { if (peopleSelected) vm.goRoot(Screen.Groups) },
            label = { Text("Группы") },
        )
    }
}

/** Ряд переключаемых меток: интересы, пол, тема группы. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChipChoice(
    options: List<Pair<String, String>>,
    isSelected: (String) -> Boolean,
    onToggle: (String) -> Unit,
) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, title) ->
            FilterChip(
                selected = isSelected(key),
                onClick = { onToggle(key) },
                label = { Text(title) },
            )
        }
    }
}

@Composable
fun Caption(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

private fun List<Interest>.asOptions() = map { it.slug to it.title }

// ==================== ПОИСК ЛЮДЕЙ ====================

/**
 * Поиск людей по имени, городу, полу, возрасту и интересам.
 *
 * Поля фильтра живут в remember, а не в rememberSaveable: сам фильтр
 * хранится в модели (peopleFilter), и при повороте экрана поля
 * восстанавливаются из неё.
 */
@Composable
fun PeopleScreen(vm: AppViewModel, state: UiState) {
    val current = state.peopleFilter
    var query by remember { mutableStateOf(current.query) }
    var city by remember { mutableStateOf(current.city) }
    var gender by remember { mutableStateOf(current.gender) }
    var ageMin by remember { mutableStateOf(current.ageMin?.toString() ?: "") }
    var ageMax by remember { mutableStateOf(current.ageMax?.toString() ?: "") }
    var interests by remember { mutableStateOf(current.interests) }
    var showFilters by remember { mutableStateOf(current.activeCount > 0) }

    fun search() = vm.searchPeople(
        PeopleFilter(
            query = query.trim(),
            city = city.trim(),
            gender = gender,
            ageMin = ageMin.trim().toIntOrNull(),
            ageMax = ageMax.trim().toIntOrNull(),
            interests = interests,
        )
    )

    CommunitySwitch(vm, peopleSelected = true)

    Panel(title = "Поиск людей") {
        Field(query, { query = it }, "Имя, @логин или город")
        if (showFilters) {
            Field(city, { city = it }, "Город (точно)")
            Caption("Пол")
            ChipChoice(GENDERS, isSelected = { it == gender }, onToggle = { gender = it })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(ageMin, { ageMin = it.filter(Char::isDigit).take(3) }, "Возраст от",
                    modifier = Modifier.weight(1f), keyboardType = KeyboardType.Number)
                Field(ageMax, { ageMax = it.filter(Char::isDigit).take(3) }, "до",
                    modifier = Modifier.weight(1f), keyboardType = KeyboardType.Number)
            }
            Caption("Интересы — подойдёт любой из отмеченных")
            ChipChoice(
                state.interests.asOptions(),
                isSelected = { it in interests },
                onToggle = { slug -> interests = if (slug in interests) interests - slug else interests + slug },
            )
            SecondaryButton("Сбросить фильтры", onClick = {
                city = ""; gender = ""; ageMin = ""; ageMax = ""; interests = emptySet()
            })
        }
        SecondaryButton(
            if (showFilters) "Скрыть фильтры"
            else "Фильтры" + (current.activeCount.takeIf { it > 0 }?.let { " ($it)" } ?: ""),
            onClick = { showFilters = !showFilters },
        )
        PrimaryButton("Найти", busy = state.busy, onClick = { search() })
    }

    Panel(title = if (state.people.isEmpty()) "Результаты" else "Найдено: ${state.people.size}") {
        if (state.people.isEmpty()) {
            EmptyNote("Никого не нашли. Попробуйте ослабить фильтры.")
        } else {
            state.people.forEachIndexed { index, person ->
                PersonRow(person) { vm.go(Screen.PersonDetail(person.id)) }
                if (index != state.people.lastIndex) HorizontalDivider()
            }
        }
        Caption(
            "В поиске только те, кто сам открыл свою анкету. Написать можно и " +
                "тому, кого здесь нет, — по точному логину в разделе «Чаты»."
        )
        SecondaryButton("Моя анкета", onClick = { vm.go(Screen.SocialProfileEdit) })
    }
}

@Composable
private fun PersonRow(person: Person, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Initials(person.displayName)
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(person.displayName, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
            val line = listOf(person.summary, person.interests.joinToString { it.title })
                .filter { it.isNotBlank() }
                .joinToString(" · ")
            if (line.isNotBlank()) Caption(line)
        }
    }
}

// ==================== АНКЕТА ЧЕЛОВЕКА ====================

@Composable
fun PersonDetailScreen(vm: AppViewModel, state: UiState, id: Int) {
    val person = state.openPerson?.takeIf { it.id == id }
    if (person == null) {
        Panel { EmptyNote("Загружаем анкету…") }
        return
    }

    Panel {
        Row(
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Initials(person.displayName, size = 64.dp)
            Column {
                Text(person.displayName, style = MaterialTheme.typography.headlineSmall)
                Caption("@${person.username}")
            }
        }
        person.age?.let { LabelValue("Возраст", ageTitle(it)) }
        if (person.city.isNotBlank()) LabelValue("Город", person.city)
        GENDERS.firstOrNull { it.first == person.gender && it.first.isNotEmpty() }
            ?.let { LabelValue("Пол", it.second) }
        if (person.about.isNotBlank()) Text(person.about, style = MaterialTheme.typography.bodyLarge)
        if (person.interests.isNotEmpty()) {
            Caption("Интересы")
            Text(person.interests.joinToString(" · ") { it.title })
        }
    }

    Panel {
        PrimaryButton("Написать", busy = state.busy, onClick = { vm.writeTo(person.username) })
        SecondaryButton("Перевести деньги", onClick = { vm.transferTo(person.username) })
    }
}

// ==================== СВОЯ АНКЕТА ====================

/**
 * Своя анкета в сообществе.
 *
 * Переключатель «показывать в поиске» — это согласие на распространение
 * персональных данных (ст. 10.1 152-ФЗ): анкету увидит любой
 * пользователь. Об этом сказано прямо под переключателем, а сервер
 * записывает согласие с адресом и временем — как при регистрации.
 */
@Composable
fun SocialProfileEditScreen(vm: AppViewModel, state: UiState) {
    val profile = state.socialProfile
    if (profile == null) {
        Panel { EmptyNote("Загружаем анкету…") }
        return
    }

    // Ключ — сама анкета: после сохранения поля берут то, что принял сервер
    var displayName by remember(profile) { mutableStateOf(profile.displayName) }
    var city by remember(profile) { mutableStateOf(profile.city) }
    var birthYear by remember(profile) { mutableStateOf(profile.birthYear?.toString() ?: "") }
    var gender by remember(profile) { mutableStateOf(profile.gender) }
    var about by remember(profile) { mutableStateOf(profile.about) }
    var interests by remember(profile) { mutableStateOf(profile.interests.map { it.slug }.toSet()) }
    var discoverable by remember(profile) { mutableStateOf(profile.isDiscoverable) }

    if (profile.isDiscoverable && !profile.distributionConsent) {
        StubBanner(
            "Согласие на распространение персональных данных отозвано, поэтому " +
                "анкета в поиске не видна. Сохраните анкету с включённым показом, " +
                "чтобы дать согласие снова."
        )
    }

    Panel(title = "Анкета") {
        Field(displayName, { displayName = it.take(60) }, "Имя для показа",
            supporting = "Если пусто — показывается логин @${profile.username}")
        Field(city, { city = it.take(60) }, "Город")
        Field(birthYear, { birthYear = it.filter(Char::isDigit).take(4) }, "Год рождения",
            keyboardType = KeyboardType.Number,
            supporting = "Другим виден только возраст")
        Caption("Пол")
        ChipChoice(
            listOf("" to "Не указан", "female" to "Женский", "male" to "Мужской"),
            isSelected = { it == gender },
            onToggle = { gender = it },
        )
        Field(about, { about = it.take(500) }, "О себе", singleLine = false)
        Caption("Интересы — до десяти")
        ChipChoice(
            state.interests.asOptions(),
            isSelected = { it in interests },
            onToggle = { slug ->
                interests = when {
                    slug in interests -> interests - slug
                    interests.size < 10 -> interests + slug
                    else -> interests
                }
            },
        )
    }

    Panel(title = "Видимость") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Показывать меня в поиске", modifier = Modifier.weight(1f))
            Switch(checked = discoverable, onCheckedChange = { discoverable = it })
        }
        Caption(
            "Включая показ, вы даёте согласие на распространение персональных " +
                "данных из анкеты: имя, возраст, город, пол, «о себе» и интересы " +
                "увидят все пользователи. Почта и баланс не показываются никогда. " +
                "Выключить показ можно в любой момент."
        )
    }

    PrimaryButton(
        "Сохранить",
        busy = state.busy,
        onClick = {
            vm.saveSocialProfile(
                SocialProfile(
                    username = profile.username,
                    displayName = displayName.trim(),
                    city = city.trim(),
                    birthYear = birthYear.toIntOrNull(),
                    gender = gender,
                    about = about.trim(),
                    interests = state.interests.filter { it.slug in interests },
                    isDiscoverable = discoverable,
                    distributionConsent = profile.distributionConsent,
                )
            )
        },
    )
}
