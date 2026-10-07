package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.STUB_NOTE
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.Money
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.StubBanner
import ru.angelhelper.app.ui.UiState

/**
 * Перевод другому пользователю.
 *
 * Единственный из трёх денежных экранов, за которым стоит настоящая
 * серверная операция. Проверок здесь намеренно мало: хватает ли денег,
 * подтверждён ли адрес почты, не приостановлены ли операции — решает
 * сервер, и повторять его правила в приложении значит завести вторую
 * их копию, которая однажды разойдётся с первой.
 */
@Composable
fun TransferScreen(vm: AppViewModel, state: UiState) {
    var receiver by rememberSaveable { mutableStateOf("") }
    var amount by rememberSaveable { mutableStateOf("") }
    var comment by rememberSaveable { mutableStateOf("") }

    Panel(title = "Доступно") {
        Money(state.dashboard?.balance ?: "—")
    }

    Panel(title = "Перевод") {
        Field(receiver, { receiver = it }, "Имя получателя")
        Field(amount, { amount = it }, "Сумма, ₽", keyboardType = KeyboardType.Decimal)
        Field(comment, { comment = it }, "Комментарий (необязательно)", singleLine = false)
        PrimaryButton(
            "Перевести",
            busy = state.busy,
            enabled = receiver.isNotBlank() && amount.isNotBlank(),
            onClick = {
                vm.transfer(receiver.trim(), amount.trim().replace(',', '.'), comment.trim())
                amount = ""
                comment = ""
            },
        )
        Text(
            "Перевод требует подтверждённого адреса почты — так же, как на сайте.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
fun TopUpScreen(vm: AppViewModel, state: UiState) {
    var amount by rememberSaveable { mutableStateOf("") }
    var method by rememberSaveable { mutableStateOf("card") }

    StubBanner(
        "Пополнение — заглушка. $STUB_NOTE На сайте платёж уходит в ЮKassa " +
            "и возвращается вебхуком; в серверном API этой точки нет."
    )

    Panel(title = "Пополнение") {
        Field(amount, { amount = it }, "Сумма, ₽", keyboardType = KeyboardType.Decimal)
        MethodChips(
            options = listOf(
                "card" to "Карта",
                "sbp" to "СБП",
                "yoomoney" to "ЮMoney",
            ),
            selected = method,
            onSelect = { method = it },
        )
        PrimaryButton(
            "Пополнить",
            busy = state.busy,
            enabled = amount.isNotBlank(),
            onClick = { vm.topUp(amount.trim().replace(',', '.'), method) },
        )
    }
}

@Composable
fun WithdrawScreen(vm: AppViewModel, state: UiState) {
    var amount by rememberSaveable { mutableStateOf("") }
    var method by rememberSaveable { mutableStateOf("card") }
    var target by rememberSaveable { mutableStateOf("") }

    StubBanner(
        "Вывод средств — заглушка. $STUB_NOTE Настоящая заявка требует " +
            "подтверждённого адреса почты, верификации и ручной обработки."
    )

    Panel(title = "Доступно") {
        Money(state.dashboard?.balance ?: "—")
    }

    Panel(title = "Заявка на вывод") {
        Field(amount, { amount = it }, "Сумма, ₽", keyboardType = KeyboardType.Decimal)
        MethodChips(
            options = listOf(
                "card" to "На карту",
                "sbp" to "СБП",
                "yoomoney" to "ЮMoney",
            ),
            selected = method,
            onSelect = { method = it },
        )
        Field(
            target, { target = it },
            when (method) {
                "sbp" -> "Номер телефона"
                "yoomoney" -> "Номер кошелька"
                else -> "Номер карты"
            },
            keyboardType = if (method == "sbp") KeyboardType.Phone else KeyboardType.Number,
        )
        PrimaryButton(
            "Создать заявку",
            busy = state.busy,
            enabled = amount.isNotBlank() && target.isNotBlank(),
            onClick = { vm.withdraw(amount.trim().replace(',', '.'), method, target.trim()) },
        )
    }
}

/**
 * Ряд способов оплаты.
 *
 * Отдельной функцией, потому что пополнение и вывод показывают один
 * и тот же набор: разойдясь, они выглядели бы как разные списки
 * поддерживаемых способов.
 */
@Composable
private fun MethodChips(
    options: List<Pair<String, String>>,
    selected: String,
    onSelect: (String) -> Unit,
) {
    // Горизонтальная прокрутка: три кнопки в ряд на узком экране
    // обрезались справа, и третий способ оплаты был не виден вовсе
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        options.forEach { (value, label) ->
            FilterChip(
                selected = selected == value,
                onClick = { onSelect(value) },
                label = { Text(label) },
            )
        }
    }
}
