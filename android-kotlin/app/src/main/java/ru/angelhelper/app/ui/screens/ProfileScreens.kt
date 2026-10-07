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
import ru.angelhelper.app.BuildConfig
import ru.angelhelper.app.data.AppPrefs
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
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

    Panel(title = "Сообщество") {
        SecondaryButton("Анкета в сообществе", onClick = { vm.go(Screen.SocialProfileEdit) })
        state.socialProfile?.userId?.takeIf { it > 0 }?.let { myId ->
            SecondaryButton("Мои записи и подписчики", onClick = { vm.go(Screen.PersonDetail(myId)) })
        }
        SecondaryButton("Чёрный список", onClick = { vm.go(Screen.BlockList) })
    }

    Panel(title = "Деньги") {
        SecondaryButton("Кошелёк", onClick = { vm.go(Screen.Dashboard) })
        SecondaryButton("Перевести деньги", onClick = { vm.go(Screen.Transfer) })
        SecondaryButton("Сборы средств", onClick = { vm.go(Screen.Fundraises) })
        SecondaryButton("История операций", onClick = { vm.go(Screen.History) })
        SecondaryButton("Верификация", onClick = { vm.go(Screen.Verification) })
        SecondaryButton("Лидеры", onClick = { vm.go(Screen.Leaders) })
    }

    Panel(title = "Прочее") {
        SecondaryButton("Мои согласия", onClick = { vm.go(Screen.Consents) })
        SecondaryButton("Настройки", onClick = { vm.go(Screen.Settings) })
    }

    Panel {
        PrimaryButton("Выйти", onClick = { vm.logout() })
    }
}

/**
 * Верификация: паспортные данные и сканы документов уходят на проверку
 * администратору — так же, как с сайта. Уровень сам не повышается:
 * его меняет администратор после проверки.
 */
@Composable
fun VerificationScreen(vm: AppViewModel, state: UiState) {
    val info = state.verification
    var fullName by rememberSaveable { mutableStateOf("") }
    var birthDate by rememberSaveable { mutableStateOf("") }
    var series by rememberSaveable { mutableStateOf("") }
    var number by rememberSaveable { mutableStateOf("") }
    var docType by rememberSaveable { mutableStateOf("") }
    val pickScan = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null && docType.isNotEmpty()) vm.uploadDocument(docType, uri)
    }

    Panel(title = "Текущий уровень") {
        LabelValue("Уровень", info?.levelTitle ?: state.profile?.verificationTitle ?: "—", strong = true)
        if (info?.passportMasked?.isNotBlank() == true) LabelValue("Паспорт", info.passportMasked)
        info?.limits?.forEach { (label, value) -> LabelValue(label, value) }
        Caption("Уровень определяет дневной предел вывода и сумму сбора, который можно опубликовать.")
    }

    Panel(title = if (info?.submitted == true) "Данные отправлены" else "Базовая верификация") {
        if (info?.submitted == true) {
            Caption("Данные на проверке. Можно отправить их заново, если ошиблись.")
        }
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
                series, { series = it.filter(Char::isDigit).take(4) }, "Серия",
                keyboardType = KeyboardType.Number,
                modifier = Modifier.weight(1f),
            )
            Field(
                number, { number = it.filter(Char::isDigit).take(6) }, "Номер",
                keyboardType = KeyboardType.Number,
                modifier = Modifier.weight(1f),
            )
        }
        PrimaryButton(
            "Отправить на проверку",
            busy = state.busy,
            enabled = fullName.isNotBlank() && birthDate.isNotBlank() && series.length == 4 && number.length == 6,
            onClick = { vm.submitVerification(fullName.trim(), birthDate.trim(), series, number) },
        )
        Caption("Паспортные данные хранятся на сервере в зашифрованном виде.")
    }

    Panel(title = "Документы") {
        info?.documents?.forEach { doc ->
            LabelValue(doc.typeTitle, "${doc.statusTitle}, ${doc.uploadedAt.asReadableDate()}")
        }
        if (info?.documents.isNullOrEmpty()) Caption("Документов пока нет.")
        Caption("Тип документа")
        ChipChoice(
            info?.documentTypes.orEmpty().map { it.id to it.title },
            isSelected = { it == docType },
            onToggle = { docType = it },
        )
        PrimaryButton(
            "Выбрать фото документа",
            busy = state.busy,
            enabled = docType.isNotEmpty(),
            onClick = { pickScan.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
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

        if (AppPrefs.DEFAULT_BASE_URL != AppPrefs.LOCAL_BASE_URL) {
            SecondaryButton("Рабочий сервер — ${AppPrefs.DEFAULT_BASE_URL}", onClick = {
                url = AppPrefs.DEFAULT_BASE_URL
                vm.setBaseUrl(AppPrefs.DEFAULT_BASE_URL)
            })
        }
        // Локальные адреса — только в отладочной сборке: в боевой HTTP
        // запрещён (network_security_config), и кнопки вели бы в никуда
        if (BuildConfig.DEBUG) {
            SecondaryButton("Телефон по кабелю — 127.0.0.1:8000", onClick = {
                url = AppPrefs.LOCAL_BASE_URL
                vm.setBaseUrl(AppPrefs.LOCAL_BASE_URL)
            })
            SecondaryButton("Эмулятор — 10.0.2.2:8000", onClick = {
                url = AppPrefs.EMULATOR_BASE_URL
                vm.setBaseUrl(AppPrefs.EMULATOR_BASE_URL)
            })
        }

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
            "Ангел-Хранитель — сообщество взаимопомощи: лента, люди, чаты " +
                "и группы, а также переводы и сборы средств.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
