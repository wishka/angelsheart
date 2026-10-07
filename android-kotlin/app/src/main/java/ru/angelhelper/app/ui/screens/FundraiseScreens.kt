package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.Fundraise
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.UiState

@Composable
fun FundraisesScreen(vm: AppViewModel, state: UiState) {
    var search by rememberSaveable { mutableStateOf(state.fundraisesSearch) }

    Panel(title = "Поиск") {
        Field(search, { search = it }, "Название или описание")
        PrimaryButton("Найти", busy = state.busy, onClick = { vm.loadFundraises(search.trim()) })
        if (search.isNotBlank()) {
            SecondaryButton("Показать все", onClick = {
                search = ""
                vm.loadFundraises("")
            })
        }
    }

    if (state.fundraises.isEmpty()) {
        Panel { EmptyNote("Сборов не нашлось.") }
    } else {
        state.fundraises.forEach { fundraise ->
            FundraiseCard(fundraise) { vm.go(Screen.FundraiseDetail(fundraise.id)) }
        }
        if (state.fundraisesNext != null) {
            SecondaryButton("Показать ещё", onClick = { vm.moreFundraises() })
        }
    }
}

@Composable
private fun FundraiseCard(fundraise: Fundraise, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                fundraise.categoryTitle,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(fundraise.title, style = MaterialTheme.typography.titleMedium)
            Text(
                fundraise.description.take(140) +
                    if (fundraise.description.length > 140) "…" else "",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Progress(fundraise)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${fundraise.currentAmount} из ${fundraise.targetAmount} ₽",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    "${fundraise.donorsCount} помогли",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun Progress(fundraise: Fundraise) {
    // Доля передаётся функцией, а не числом: в Material3 версии 1.3
    // прежняя запись LinearProgressIndicator(progress = 0.5f) объявлена
    // устаревшей именно в пользу этой.
    val share = (fundraise.progressPercent.coerceIn(0, 100)) / 100f
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        LinearProgressIndicator(
            progress = { share },
            modifier = Modifier
                .fillMaxWidth()
                .height(8.dp),
        )
        Text(
            "${fundraise.progressPercent}%",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * Карточка сбора и форма пожертвования.
 *
 * Данные берутся из state.openFundraise, а не из списка: открытый сбор
 * перезапрашивается после пожертвования, и показывать рядом устаревшую
 * запись из списка означало бы оставить на экране прежнюю собранную
 * сумму сразу после того, как человек её увеличил.
 */
@Composable
fun FundraiseDetailScreen(vm: AppViewModel, state: UiState, id: Int) {
    val fundraise = state.openFundraise?.takeIf { it.id == id }
    // Ключ по номеру сбора обязателен: без него поля живут в одной
    // позиции композиции, и, перейдя со сбора на сбор, человек видит
    // в поле сумму, набранную для предыдущего.
    var amount by rememberSaveable(id) { mutableStateOf("") }
    var message by rememberSaveable(id) { mutableStateOf("") }
    var anonymous by rememberSaveable(id) { mutableStateOf(false) }

    if (fundraise == null) {
        Panel { EmptyNote("Загружаем сбор…") }
        return
    }

    Panel(title = fundraise.title) {
        Text(
            fundraise.categoryTitle,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.primary,
        )
        Text(fundraise.description, style = MaterialTheme.typography.bodyLarge)
        Progress(fundraise)
        LabelValue("Собрано", "${fundraise.currentAmount} ₽", strong = true)
        LabelValue("Цель", "${fundraise.targetAmount} ₽")
        LabelValue("Помогли", "${fundraise.donorsCount} человек")
        LabelValue("Автор", fundraise.authorName)
    }

    Panel(title = "Помочь") {
        Field(
            amount, { amount = it }, "Сумма, ₽",
            keyboardType = KeyboardType.Decimal,
            supporting = "Комиссию сервиса удерживает сервер",
        )
        Field(message, { message = it }, "Слова поддержки (необязательно)", singleLine = false)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = anonymous, onCheckedChange = { anonymous = it })
            Text("Помочь анонимно", style = MaterialTheme.typography.bodyMedium)
        }
        PrimaryButton(
            "Помочь",
            busy = state.busy,
            enabled = amount.isNotBlank(),
            onClick = {
                vm.donate(id, amount.trim().replace(',', '.'), message.trim(), anonymous)
                amount = ""
                message = ""
            },
        )
    }
}
