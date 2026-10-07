package ru.angelhelper.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
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
import ru.angelhelper.app.ui.AppViewModel
import ru.angelhelper.app.ui.Field
import ru.angelhelper.app.ui.Panel
import ru.angelhelper.app.ui.PrimaryButton
import ru.angelhelper.app.ui.Screen
import ru.angelhelper.app.ui.SecondaryButton
import ru.angelhelper.app.ui.StubBanner
import ru.angelhelper.app.ui.UiState

/**
 * Вход.
 *
 * Поле кода двухфакторной проверки показывается не всегда: сервер
 * отвечает 403 и просит код только тем, у кого она включена. Показывать
 * его сразу всем означало бы пугать поле, которое почти никому не нужно,
 * — поэтому оно раскрывается по переключателю.
 */
@Composable
fun LoginScreen(vm: AppViewModel, state: UiState) {
    var username by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    var manualCode by rememberSaveable { mutableStateOf(false) }
    // Поле показывается, когда сервер ответил «нужен код», либо когда
    // человек раскрыл его сам. Первое важнее: догадаться нажать
    // переключатель, о котором не знаешь, нельзя.
    val needCode = manualCode || state.twoFactorRequired

    if (state.demoMode) {
        StubBanner(
            "Демо-режим включён: данные выдуманы, сеть не используется. " +
                "Войти можно с любым именем и паролем."
        )
    }

    Panel(title = "Вход") {
        Field(username, { username = it }, "Имя пользователя")
        Field(password, { password = it }, "Пароль", password = true)

        if (needCode) {
            Field(
                code, { code = it }, "Код из приложения",
                keyboardType = KeyboardType.Number,
                supporting = "Шесть цифр или резервный код",
            )
        }

        PrimaryButton("Войти", busy = state.busy, onClick = {
            vm.login(username.trim(), password, code.trim())
        })

        SecondaryButton(
            if (needCode) "Скрыть поле кода" else "У меня включена двухфакторная проверка",
            onClick = {
                manualCode = !needCode
                // Старый код не должен уехать на сервер следующей
                // попыткой: просроченный код даст отказ на пустом месте
                if (needCode) {
                    code = ""
                    vm.setTwoFactorRequired(false)
                }
            },
        )
    }

    Panel {
        SecondaryButton("Создать учётную запись", onClick = { vm.go(Screen.Register) })
        SecondaryButton("Забыли пароль?", onClick = { vm.go(Screen.PasswordReset) })
        Text(
            "Сервер: ${state.baseUrl}",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        SecondaryButton("Изменить адрес сервера", onClick = { vm.go(Screen.Settings) })
    }
}

/**
 * Регистрация.
 *
 * Согласия — одним флажком, и это осознанное упрощение для тестового
 * приложения: на сайте их три отдельных документа с разными правовыми
 * основаниями. Сервер всё равно требует оба обязательных, поэтому снять
 * флажок и зарегистрироваться не получится.
 */
@Composable
fun RegisterScreen(vm: AppViewModel, state: UiState) {
    var username by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var agreed by rememberSaveable { mutableStateOf(false) }

    Panel(title = "Регистрация") {
        Field(username, { username = it }, "Имя пользователя")
        Field(
            email, { email = it }, "Адрес почты",
            keyboardType = KeyboardType.Email,
            supporting = "На него придёт письмо для подтверждения",
        )
        Field(
            password, { password = it }, "Пароль", password = true,
            supporting = "Сервер проверяет пароль так же, как на сайте",
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Checkbox(checked = agreed, onCheckedChange = { agreed = it })
            Column {
                Text(
                    "Принимаю Пользовательское соглашение и согласен " +
                        "на обработку персональных данных",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }

        PrimaryButton(
            "Зарегистрироваться",
            enabled = agreed,
            busy = state.busy,
            onClick = { vm.register(username.trim(), email.trim(), password, agreed) },
        )
        SecondaryButton("Уже есть учётная запись", onClick = { vm.back() })
    }
}

@Composable
fun PasswordResetScreen(vm: AppViewModel, state: UiState) {
    var email by rememberSaveable { mutableStateOf("") }

    StubBanner(
        "Восстановление пароля работает только на сайте: ссылка из письма " +
            "открывается в браузере. Здесь экран показан целиком, но " +
            "письмо приложение не отправляет."
    )

    Panel(title = "Восстановление пароля") {
        Field(email, { email = it }, "Адрес почты", keyboardType = KeyboardType.Email)
        PrimaryButton(
            "Отправить ссылку",
            busy = state.busy,
            onClick = { vm.resetPassword(email.trim()) },
        )
        SecondaryButton("Вернуться ко входу", onClick = { vm.back() })
    }
}
