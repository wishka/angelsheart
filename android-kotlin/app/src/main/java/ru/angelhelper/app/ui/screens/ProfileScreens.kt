package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import ru.angelhelper.app.data.AppPrefs
import ru.angelhelper.app.data.STUB_NOTE
import ru.angelhelper.app.data.asReadableDate
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.EmptyNote
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.LabelValue
import ru.angelhelper.app.ui.Money
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.StubBanner
import ru.angelhelper.app.ui.UiState

@Composable
fun ProfileScreen(vm: AppViewModel, state: UiState) {
    val profile = state.profile

    Panel(title = "Учётная запись") {
        if (profile == null) {
            EmptyNote("Загружаем профиль…")
        } else {
            LabelValue("Имя пользователя", profile.username, strong = true)
            LabelValue("Адрес почты", profile.email)
            LabelValue("Верификация", profile.verificationTitle)
            LabelValue("С нами с", profile.dateJoined.asReadableDate())
            Money(profile.balance)
        }
        SecondaryButton("Обновить", onClick = { vm.loadProfile() })
    }

    Panel(title = "Разделы") {
        SecondaryButton("Анкета в сообществе", onClick = { vm.go(Screen.SocialProfileEdit) })
        SecondaryButton("Чёрный список", onClick = { vm.go(Screen.BlockList) })
        SecondaryButton("Группы", onClick = { vm.goRoot(Screen.Groups) })
        SecondaryButton("История операций", onClick = { vm.go(Screen.History) })
        SecondaryButton("Верификация", onClick = { vm.go(Screen.Verification) })
        SecondaryButton("Мои согласия", onClick = { vm.go(Screen.Consents) })
        SecondaryButton("Лидеры", onClick = { vm.go(Screen.Leaders) })
        SecondaryButton("Настройки", onClick = { vm.go(Screen.Settings) })
    }

    Panel {
        PrimaryButton("Выйти", onClick = { vm.logout() })
    }
}

@Composable
fun VerificationScreen(vm: AppViewModel, state: UiState) {
    var fullName by rememberSaveable { mutableStateOf("") }
    var birthDate by rememberSaveable { mutableStateOf("") }
    var series by rememberSaveable { mutableStateOf("") }
    var number by rememberSaveable { mutableStateOf("") }

    StubBanner(
        "Подача паспортных данных — заглушка. $STUB_NOTE Настоящая " +
            "верификация проходит только на сайте: данные там шифруются " +
            "на сервере, и передавать их из тестового приложения незачем."
    )

    Panel(title = "Текущий уровень") {
        LabelValue("Уровень", state.profile?.verificationTitle ?: "—", strong = true)
        Text(
            "Уровень определяет дневной предел вывода и сумму сбора, " +
                "который можно опубликовать.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    Panel(title = "Базовая верификация") {
        Field(fullName, { fullName = it }, "Фамилия, имя, отчество")
        Field(
            birthDate, { birthDate = it }, "Дата рождения",
            keyboardType = KeyboardType.Number,
            supporting = "В виде 31.12.1990. Сервис доступен с 18 лет",
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Field(
                series, { series = it }, "Серия",
                keyboardType = KeyboardType.Number,
                modifier = Modifier.weight(1f),
            )
            Field(
                number, { number = it }, "Номер",
                keyboardType = KeyboardType.Number,
                modifier = Modifier.weight(1f),
            )
        }
        PrimaryButton(
            "Отправить на проверку",
            busy = state.busy,
            enabled = fullName.isNotBlank(),
            onClick = { vm.submitVerification(fullName.trim(), birthDate.trim(), series.trim(), number.trim()) },
        )
    }
}

@Composable
fun ConsentsScreen(vm: AppViewModel, state: UiState) {
    Panel(title = "Мои согласия") {
        if (state.consents.isEmpty()) {
            EmptyNote("Согласий пока нет.")
        } else {
            state.consents.forEachIndexed { index, consent ->
                LabelValue(
                    consent.title,
                    if (consent.accepted) {
                        "выдано ${consent.agreedAt.asReadableDate()}, ред. ${consent.version}"
                    } else {
                        "не выдано"
                    },
                )
                if (index != state.consents.lastIndex) HorizontalDivider()
            }
        }
        Text(
            "Выдать или отозвать согласие можно на сайте: там показан " +
                "полный текст документа, который вы подписываете.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        SecondaryButton("Обновить", onClick = { vm.loadConsents() })
    }
}

/**
 * Настройки: адрес сервера и демо-режим.
 *
 * Самый важный экран тестового приложения. На телефоне «localhost» —
 * это сам телефон, а не ноутбук с Django, и без объяснения этой разницы
 * приложение выглядит сломанным. Готовые кнопки ставят оба рабочих
 * варианта, чтобы не набирать адрес руками на телефонной клавиатуре.
 */
@Composable
fun SettingsScreen(vm: AppViewModel, state: UiState) {
    var url by rememberSaveable(state.baseUrl) { mutableStateOf(state.baseUrl) }

    Panel(title = "Адрес сервера") {
        Field(
            url, { url = it }, "Адрес",
            supporting = "Например, http://127.0.0.1:8000/",
        )
        PrimaryButton("Сохранить", onClick = { vm.setBaseUrl(url) })

        SecondaryButton("Телефон по кабелю — 127.0.0.1:8000", onClick = {
            url = AppPrefs.DEFAULT_BASE_URL
            vm.setBaseUrl(AppPrefs.DEFAULT_BASE_URL)
        })
        SecondaryButton("Эмулятор — 10.0.2.2:8000", onClick = {
            url = AppPrefs.EMULATOR_BASE_URL
            vm.setBaseUrl(AppPrefs.EMULATOR_BASE_URL)
        })

        Text(
            "На телефоне localhost — это сам телефон, а не компьютер. " +
                "Чтобы заработал адрес 127.0.0.1, выполните на компьютере " +
                "`adb reverse tcp:8000 tcp:8000` — телефон пробросит этот " +
                "порт на машину с Django. В эмуляторе компьютер виден как " +
                "10.0.2.2. По Wi-Fi укажите адрес компьютера в локальной " +
                "сети и запустите сервер на 0.0.0.0.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    Panel(title = "Демо-режим") {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Данные из памяти, без сети", style = MaterialTheme.typography.bodyLarge)
            Switch(checked = state.demoMode, onCheckedChange = { vm.setDemoMode(it) })
        }
        Text(
            "Включите, чтобы посмотреть приложение без запущенного сервера. " +
                "Переключение выбрасывает из учётной записи: токен одного " +
                "режима в другом бессмыслен.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    Panel(title = "О приложении") {
        LabelValue("Версия", "1.0.0")
        LabelValue("Режим", if (state.demoMode) "демо" else "сервер")
        Text(
            "Тестовая сборка. Пополнение, вывод, верификация и " +
                "восстановление пароля — заглушки: в серверном API этих " +
                "операций нет.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
