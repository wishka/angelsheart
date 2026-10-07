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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.TextButton
import ru.angelhelper.app.data.Withdrawal
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.Money
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.UiState

/**
 * Перевод другому пользователю.
 *
 * Проверок здесь намеренно мало: хватает ли денег,
 * подтверждён ли адрес почты, не приостановлены ли операции — решает
 * сервер, и повторять его правила в приложении значит завести вторую
 * их копию, которая однажды разойдётся с первой.
 */
@Composable
fun TransferScreen(vm: AppViewModel, state: UiState) {
    // Получатель подставляется из анкеты или чата («Перевести деньги»)
    var receiver by rememberSaveable { mutableStateOf(state.transferPrefill) }
    LaunchedEffect(Unit) {
        if (state.transferPrefill.isNotEmpty()) vm.consumeTransferPrefill()
    }
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
    val wallet = state.wallet
    var amount by rememberSaveable { mutableStateOf("") }
    var method by rememberSaveable { mutableStateOf("") }
    val methods = wallet?.topupMethods.orEmpty()
    val selected = method.ifEmpty { methods.firstOrNull()?.id ?: "" }

    Panel(title = "Баланс") {
        Money(wallet?.balance ?: state.dashboard?.balance ?: "—")
    }

    Panel(title = "Пополнение") {
        Field(
            amount, { amount = it }, "Сумма, ₽",
            keyboardType = KeyboardType.Decimal,
            supporting = wallet?.let { "От ${it.topupMin} до ${it.topupMax} ₽" },
        )
        MethodChips(
            options = methods.map { it.id to it.title },
            selected = selected,
            onSelect = { method = it },
        )
        PrimaryButton(
            "Перейти к оплате",
            busy = state.busy,
            enabled = amount.isNotBlank() && selected.isNotEmpty(),
            onClick = { vm.topUp(amount.trim().replace(',', '.'), selected) },
        )
        Caption(
            if (wallet?.topupSimulated == true) {
                "Сервер в тестовом режиме: деньги зачислятся сразу, без оплаты."
            } else {
                "Оплата проходит на странице ЮKassa в браузере. Деньги поступят " +
                    "после подтверждения платежа — обновите «Кошелёк» через минуту."
            }
        )
    }
}

/**
 * Вывод средств: форма заявки и список своих заявок.
 *
 * Поля зависят от способа — как в форме сайта, и сервер проверяет их по
 * тем же правилам (WithdrawalForm, лимиты, антифрод). Деньги на время
 * рассмотрения замораживаются; отменить можно, пока заявка не в работе.
 */
@Composable
fun WithdrawScreen(vm: AppViewModel, state: UiState) {
    val wallet = state.wallet
    val methods = wallet?.withdrawMethods.orEmpty()
    var method by rememberSaveable { mutableStateOf("") }
    val selected = method.ifEmpty { methods.firstOrNull()?.id ?: "" }
    var amount by rememberSaveable { mutableStateOf("") }
    var cardNumber by rememberSaveable { mutableStateOf("") }
    var cardHolder by rememberSaveable { mutableStateOf("") }
    var expiry by rememberSaveable { mutableStateOf("") }
    var phone by rememberSaveable { mutableStateOf("") }
    var bank by rememberSaveable { mutableStateOf("") }
    var walletNumber by rememberSaveable { mutableStateOf("") }

    Panel(title = "Доступно") {
        Money(wallet?.balance ?: state.dashboard?.balance ?: "—")
        wallet?.let { Caption("Вывод от ${it.withdrawMin} до ${it.withdrawMax} ₽ за раз") }
    }

    Panel(title = "Заявка на вывод") {
        Field(amount, { amount = it }, "Сумма, ₽", keyboardType = KeyboardType.Decimal)
        MethodChips(
            options = methods.map { it.id to it.title },
            selected = selected,
            onSelect = { method = it },
        )
        when (selected) {
            "card" -> {
                Field(cardNumber, { cardNumber = it.filter { c -> c.isDigit() || c == ' ' }.take(23) },
                    "Номер карты", keyboardType = KeyboardType.Number)
                Field(cardHolder, { cardHolder = it.uppercase().take(100) }, "Имя на карте",
                    supporting = "Латиницей, как на карте")
                Field(expiry, { expiry = it.take(5) }, "Срок действия", supporting = "ММ/ГГ")
            }
            "sbp" -> {
                Field(phone, { phone = it.take(20) }, "Номер телефона", keyboardType = KeyboardType.Phone)
                Caption("Банк получателя")
                MethodChips(
                    options = wallet?.sbpBanks.orEmpty().map { it.id to it.title },
                    selected = bank,
                    onSelect = { bank = it },
                )
            }
            "yoomoney" -> Field(walletNumber, { walletNumber = it.filter(Char::isDigit).take(20) },
                "Номер кошелька", keyboardType = KeyboardType.Number)
        }
        PrimaryButton(
            "Создать заявку",
            busy = state.busy,
            enabled = amount.isNotBlank() && selected.isNotEmpty(),
            onClick = {
                val fields = buildMap {
                    put("payment_method", selected)
                    put("amount", amount.trim().replace(',', '.'))
                    when (selected) {
                        "card" -> {
                            put("card_number", cardNumber.trim())
                            put("card_holder", cardHolder.trim())
                            put("expiry_date", expiry.trim())
                        }
                        "sbp" -> {
                            put("phone_number", phone.trim())
                            put("bank_id", bank)
                        }
                        "yoomoney" -> put("wallet_number", walletNumber.trim())
                    }
                }
                vm.createWithdrawal(fields) {
                    amount = ""
                    cardNumber = ""
                    expiry = ""
                }
            },
        )
        Caption(
            "Нужны подтверждённая почта и верификация. Сумма замораживается до " +
                "решения по заявке; обычно её обрабатывают в течение рабочего дня."
        )
    }

    Panel(title = "Мои заявки") {
        if (state.withdrawals.isEmpty()) {
            EmptyNote("Заявок пока нет.")
        } else {
            state.withdrawals.forEachIndexed { index, item ->
                WithdrawalRow(item, onCancel = { vm.cancelWithdrawal(item.id) })
                if (index != state.withdrawals.lastIndex) HorizontalDivider()
            }
        }
    }
}

@Composable
private fun WithdrawalRow(item: Withdrawal, onCancel: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        LabelValue("${item.amount} ₽", item.statusTitle, strong = true)
        Caption(listOf(item.methodTitle, item.details, item.createdAt.asReadableDate())
            .filter { it.isNotBlank() }.joinToString(" · "))
        if (item.canCancel) {
            TextButton(onClick = onCancel) { Text("Отменить заявку") }
        }
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
