package ru.angelhelper.app.data

import kotlinx.coroutines.delay

/**
 * Ответы заглушек, общие для демо-режима и для живого сервера.
 *
 * Вынесены отдельно, чтобы операция, которой на сервере нет, отвечала
 * одинаково в обоих режимах. Иначе «пополнение» вело бы себя по-разному
 * в зависимости от переключателя, и разница выглядела бы как настоящая
 * работа сервера.
 */
object Demo {

    fun topUpReply(amount: String): Outcome<String> = Outcome.Ok(
        "Пополнение на $amount ₽ принято.\n\n$STUB_NOTE\n\n" +
            "На сайте эта операция уходит в ЮKassa и возвращается вебхуком; " +
            "в API соответствующей точки нет."
    )

    fun withdrawReply(amount: String): Outcome<String> = Outcome.Ok(
        "Заявка на вывод $amount ₽ создана.\n\n$STUB_NOTE\n\n" +
            "Настоящий вывод требует подтверждённого адреса почты, " +
            "верификации и обработки заявки вручную."
    )

    fun verificationReply(): Outcome<String> = Outcome.Ok(
        "Данные отправлены на проверку.\n\n$STUB_NOTE\n\n" +
            "Паспортные данные принимает только сайт: они шифруются " +
            "на сервере, и передавать их из тестового приложения незачем."
    )

    fun passwordResetReply(email: String): Outcome<String> = Outcome.Ok(
        "Если адрес $email зарегистрирован, письмо со ссылкой отправлено.\n\n" +
            "$STUB_NOTE\n\nВосстановление пароля работает на сайте: " +
            "ссылка из письма открывается в браузере."
    )
}

/**
 * Выдуманные данные из памяти.
 *
 * Нужны, чтобы приложение можно было открыть и пролистать вообще без
 * сервера — на чужом телефоне, в самолёте, на демонстрации. Данные
 * заведомо ненастоящие: имена вымышленные, суммы круглые.
 *
 * Состояние живёт в объекте, то есть переживает переходы между экранами
 * и сбрасывается при перезапуске приложения. Перевод и пожертвование
 * действительно меняют баланс — иначе проверить поведение экрана после
 * операции было бы нельзя.
 */
object DemoApi : Api {

    private const val PAUSE_MS = 350L

    private var balance = 12_500.0
    private var sent = 3_400.0
    private var received = 15_900.0

    private val transactions = mutableListOf(
        Tx(1, "demo_user", "anna_petrova", "1200.00", "На лекарства",
            "2026-09-14T10:15:00Z", "completed", false),
        Tx(2, "sergey_ivanov", "demo_user", "5000.00", "Спасибо за помощь",
            "2026-09-12T18:40:00Z", "completed", false),
        Tx(3, "demo_user", "Сбор «Лечение Ани»", "2200.00", "Пожертвование",
            "2026-09-10T09:05:00Z", "completed", true),
    )

    private val fundraises = mutableListOf(
        Fundraise(1, "Лечение Ани", "Реабилитация после операции. Нужен курс " +
            "занятий с врачом и ортопедический аппарат.", "medical",
            "300000.00", "184500.00", 61, "anna_petrova", 47, "active",
            "2026-08-20T12:00:00Z"),
        Fundraise(2, "Приют «Тёплый нос»", "Корм и лекарства для сорока собак " +
            "на зиму. Приют живёт только на пожертвования.", "animal",
            "120000.00", "96300.00", 80, "priut_teplo", 132, "active",
            "2026-09-01T09:30:00Z"),
        Fundraise(3, "Учебники для сельской школы", "Комплект учебников для " +
            "двух классов: в школе их не хватает третий год.", "education",
            "60000.00", "12400.00", 20, "shkola_47", 18, "active",
            "2026-09-08T15:10:00Z"),
        Fundraise(4, "Восстановление после пожара", "Семья из четырёх человек " +
            "осталась без дома. Собираем на самое необходимое.", "other",
            "450000.00", "421000.00", 93, "pomosh_ruka", 268, "active",
            "2026-07-30T08:00:00Z"),
    )

    // Locale.US обязателен: при русской локали String.format ставит
    // запятую, и следующий же toDouble() падает с NumberFormatException.
    // Разделитель для показа человеку подставляет экран, а не эти данные.
    private fun money(value: Double): String =
        String.format(java.util.Locale.US, "%.2f", value)

    override suspend fun login(username: String, password: String, code: String): Outcome<Tokens> {
        delay(PAUSE_MS)
        if (username.isBlank() || password.isBlank()) {
            return Outcome.Fail("Введите имя пользователя и пароль")
        }
        return Outcome.Ok(Tokens(access = "demo-access", refresh = "demo-refresh"))
    }

    override suspend fun register(
        username: String,
        email: String,
        password: String,
        acceptTerms: Boolean,
        consentDataProcessing: Boolean,
    ): Outcome<Tokens> {
        delay(PAUSE_MS)
        if (!acceptTerms || !consentDataProcessing) {
            return Outcome.Fail("Без согласий регистрация невозможна")
        }
        return Outcome.Ok(Tokens(access = "demo-access", refresh = "demo-refresh"))
    }

    override suspend fun logout(): Outcome<Unit> {
        delay(PAUSE_MS)
        return Outcome.Ok(Unit)
    }

    override suspend fun me(): Outcome<Profile> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            Profile(
                id = 1,
                username = "demo_user",
                email = "demo@example.com",
                balance = money(balance),
                verificationLevel = "basic",
                dateJoined = "2026-05-01T10:00:00Z",
            )
        )
    }

    override suspend fun dashboard(): Outcome<Dashboard> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            Dashboard(
                balance = money(balance),
                totalSent = money(sent),
                totalReceived = money(received),
                recent = transactions.take(10),
            )
        )
    }

    override suspend fun fundraises(search: String): Outcome<List<Fundraise>> {
        delay(PAUSE_MS)
        if (search.isBlank()) return Outcome.Ok(fundraises.toList())
        val needle = search.trim().lowercase()
        return Outcome.Ok(
            fundraises.filter {
                it.title.lowercase().contains(needle) || it.description.lowercase().contains(needle)
            }
        )
    }

    override suspend fun fundraise(id: Int): Outcome<Fundraise> {
        delay(PAUSE_MS)
        val found = fundraises.firstOrNull { it.id == id }
        return if (found == null) Outcome.Fail("Сбор не найден") else Outcome.Ok(found)
    }

    override suspend fun donate(
        id: Int,
        amount: String,
        message: String,
        anonymous: Boolean,
    ): Outcome<String> {
        delay(PAUSE_MS)
        val value = amount.replace(',', '.').toDoubleOrNull()
            ?: return Outcome.Fail("Введите сумму")
        if (value <= 0) return Outcome.Fail("Сумма должна быть больше нуля")
        if (value > balance) {
            return Outcome.Fail("Недостаточно средств. Доступно ${money(balance)} ₽")
        }
        val index = fundraises.indexOfFirst { it.id == id }
        if (index < 0) return Outcome.Fail("Сбор не найден")

        val fundraise = fundraises[index]
        val collected = fundraise.currentAmount.toDouble() + value
        val target = fundraise.targetAmount.toDouble()
        fundraises[index] = fundraise.copy(
            currentAmount = money(collected),
            donorsCount = fundraise.donorsCount + 1,
            progressPercent = if (target > 0) ((collected / target) * 100).toInt() else 0,
        )

        balance -= value
        sent += value
        transactions.add(
            0,
            Tx(
                id = transactions.size + 1,
                senderName = "demo_user",
                receiverName = "Сбор «${fundraise.title}»",
                amount = money(value),
                comment = message.ifBlank { "Пожертвование" },
                createdAt = "только что",
                status = "completed",
                isDonation = true,
            )
        )
        return Outcome.Ok("Пожертвование ${money(value)} ₽ отправлено (демо-режим)")
    }

    override suspend fun transfer(
        receiver: String,
        amount: String,
        comment: String,
    ): Outcome<String> {
        delay(PAUSE_MS)
        if (receiver.isBlank()) return Outcome.Fail("Укажите, кому переводите деньги")
        val value = amount.replace(',', '.').toDoubleOrNull()
            ?: return Outcome.Fail("Введите сумму")
        if (value <= 0) return Outcome.Fail("Сумма должна быть больше нуля")
        if (value > balance) {
            return Outcome.Fail("Недостаточно средств. Доступно ${money(balance)} ₽")
        }

        balance -= value
        sent += value
        transactions.add(
            0,
            Tx(
                id = transactions.size + 1,
                senderName = "demo_user",
                receiverName = receiver,
                amount = money(value),
                comment = comment,
                createdAt = "только что",
                status = "completed",
                isDonation = false,
            )
        )
        return Outcome.Ok("Перевод ${money(value)} ₽ пользователю $receiver выполнен (демо-режим)")
    }

    override suspend fun transactions(): Outcome<List<Tx>> {
        delay(PAUSE_MS)
        return Outcome.Ok(transactions.toList())
    }

    override suspend fun leaders(): Outcome<List<Leader>> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            listOf(
                Leader("sergey_ivanov", "48200.00"),
                Leader("maria_k", "31500.00"),
                Leader("demo_user", money(sent)),
                Leader("pavel_r", "9800.00"),
            )
        )
    }

    override suspend fun consents(): Outcome<List<Consent>> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            listOf(
                Consent(1, "Пользовательское соглашение", "1.1", "2026-05-01", true),
                Consent(2, "Обработка персональных данных", "1.1", "2026-05-01", true),
                Consent(3, "Использование cookies", "1.1", "2026-05-01", true),
                Consent(4, "Распространение персональных данных", "1.1", "", false),
            )
        )
    }

    override suspend fun topUp(amount: String, method: String): Outcome<String> {
        delay(PAUSE_MS)
        val value = amount.replace(',', '.').toDoubleOrNull()
            ?: return Outcome.Fail("Введите сумму")
        if (value <= 0) return Outcome.Fail("Сумма должна быть больше нуля")
        balance += value
        return Demo.topUpReply(money(value))
    }

    override suspend fun withdraw(
        amount: String,
        method: String,
        target: String,
    ): Outcome<String> {
        delay(PAUSE_MS)
        val value = amount.replace(',', '.').toDoubleOrNull()
            ?: return Outcome.Fail("Введите сумму")
        if (value <= 0) return Outcome.Fail("Сумма должна быть больше нуля")
        if (value > balance) {
            return Outcome.Fail("Недостаточно средств. Доступно ${money(balance)} ₽")
        }
        return Demo.withdrawReply(money(value))
    }

    override suspend fun submitVerification(
        fullName: String,
        birthDate: String,
        series: String,
        number: String,
    ): Outcome<String> {
        delay(PAUSE_MS)
        if (fullName.isBlank()) return Outcome.Fail("Укажите полное имя")
        return Demo.verificationReply()
    }

    override suspend fun resetPassword(email: String): Outcome<String> {
        delay(PAUSE_MS)
        if (!email.contains("@")) return Outcome.Fail("Введите адрес почты")
        return Demo.passwordResetReply(email)
    }
}
