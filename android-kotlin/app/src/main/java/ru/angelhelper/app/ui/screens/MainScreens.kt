package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.Tx
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.Money
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.StubBanner
import ru.angelhelper.app.ui.UiState

/**
 * Главная: баланс, оборот и последние операции.
 *
 * Список здесь обычным Column, а не LazyColumn: весь экран уже лежит
 * в прокручиваемом контейнере, и вложенная ленивая прокрутка в Compose
 * падает с исключением о бесконечной высоте. Операций тут не больше
 * десяти — ленивость не нужна.
 */
@Composable
fun DashboardScreen(vm: AppViewModel, state: UiState) {
    val data = state.dashboard

    if (state.demoMode) {
        StubBanner("Демо-режим: данные выдуманы, сеть не используется.")
    }

    Panel(title = "Ваш баланс") {
        Money(data?.balance ?: "—")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                PrimaryButton("Пополнить", onClick = { vm.go(Screen.TopUp) })
            }
            Column(modifier = Modifier.weight(1f)) {
                PrimaryButton("Вывести", onClick = { vm.go(Screen.Withdraw) })
            }
        }
    }

    Panel(title = "Ваша помощь") {
        LabelValue("Отправлено", "${data?.totalSent ?: "—"} ₽", strong = true)
        LabelValue("Получено", "${data?.totalReceived ?: "—"} ₽", strong = true)
    }

    Panel(title = "Последние операции") {
        val recent = data?.recent.orEmpty()
        if (recent.isEmpty()) {
            EmptyNote("Пока операций нет. Начните с перевода или пожертвования.")
        } else {
            recent.forEachIndexed { index, tx ->
                TxRow(tx, vm.currentUsername)
                if (index != recent.lastIndex) HorizontalDivider()
            }
        }
        // go, а не goRoot: истории больше нет на нижней панели, и из
        // корневого экрана без вкладки не было бы пути «назад»
        SecondaryButton("Вся история", onClick = { vm.go(Screen.History) })
    }

    Panel(title = "Ещё") {
        // Перевод ушёл с нижней панели, освободив место «Людям» и «Чатам»
        SecondaryButton("Перевести деньги", onClick = { vm.go(Screen.Transfer) })
        SecondaryButton("Сборы средств", onClick = { vm.goRoot(Screen.Fundraises) })
        SecondaryButton("Лидеры", onClick = { vm.go(Screen.Leaders) })
        SecondaryButton("Обновить", onClick = { vm.refreshDashboard() })
    }
}

/**
 * Одна операция списком.
 *
 * Направление определяется по имени получателя: сервер не присылает
 * отдельного признака «мне или от меня», а показывать «+» и «−» без
 * этого нельзя — человек не поймёт, убыло у него или прибыло.
 */
@Composable
private fun TxRow(tx: Tx, currentUsername: String) {
    val incoming = tx.incomingFor(currentUsername)
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Top,
        ) {
            Text(
                if (incoming) "От ${tx.senderName}" else "Кому: ${tx.receiverName}",
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f),
            )
            Text(
                (if (incoming) "+" else "−") + " ${tx.amount} ₽",
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Bold,
                color = if (incoming) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurface
                },
            )
        }
        val note = listOfNotNull(
            tx.comment.takeIf { it.isNotBlank() },
            tx.createdAt.takeIf { it.isNotBlank() }?.asReadableDate(),
            tx.statusTitle.takeIf { tx.status != "completed" },
        ).joinToString(" · ")
        if (note.isNotBlank()) {
            Text(
                note,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
fun HistoryScreen(vm: AppViewModel, state: UiState) {
    Panel(title = "История операций") {
        if (state.transactions.isEmpty()) {
            EmptyNote("Операций пока нет.")
        } else {
            state.transactions.forEachIndexed { index, tx ->
                TxRow(tx, vm.currentUsername)
                if (index != state.transactions.lastIndex) HorizontalDivider()
            }
        }
        SecondaryButton("Обновить", onClick = { vm.loadTransactions() })
    }
}

@Composable
fun LeadersScreen(vm: AppViewModel, state: UiState) {
    Panel(title = "Самые щедрые") {
        if (state.leaders.isEmpty()) {
            EmptyNote("Список пуст.")
        } else {
            state.leaders.forEachIndexed { index, leader ->
                LabelValue("${index + 1}. ${leader.username}", "${leader.amount} ₽", strong = true)
            }
        }
        SecondaryButton("Обновить", onClick = { vm.loadLeaders() })
    }
}
