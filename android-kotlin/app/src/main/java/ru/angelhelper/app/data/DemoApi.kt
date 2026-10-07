package ru.angelhelper.app.data

import kotlinx.coroutines.delay

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

    // Демо-списки короткие и приходят одной страницей (next = null)
    override suspend fun fundraises(search: String, page: Int): Outcome<Page<Fundraise>> {
        delay(PAUSE_MS)
        val needle = search.trim().lowercase()
        return Outcome.Ok(
            Page(
                fundraises.filter {
                    needle.isEmpty() || it.title.lowercase().contains(needle) ||
                        it.description.lowercase().contains(needle)
                },
                null,
            )
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

    override suspend fun transactions(page: Int): Outcome<Page<Tx>> {
        delay(PAUSE_MS)
        return Outcome.Ok(Page(transactions.toList(), null))
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

    // ==================== СООБЩЕСТВО ====================
    // Логика — в DemoSocial: здесь только пауза, похожая на сеть.

    override suspend fun interests(): Outcome<List<Interest>> = Outcome.Ok(DemoSocial.interests)

    override suspend fun socialProfile(): Outcome<SocialProfile> {
        delay(PAUSE_MS)
        return DemoSocial.profile()
    }

    override suspend fun saveSocialProfile(profile: SocialProfile): Outcome<SocialProfile> {
        delay(PAUSE_MS)
        return DemoSocial.saveProfile(profile)
    }

    override suspend fun people(filter: PeopleFilter, page: Int): Outcome<Page<Person>> {
        delay(PAUSE_MS)
        return DemoSocial.people(filter).asPage()
    }

    private fun <T> Outcome<List<T>>.asPage(): Outcome<Page<T>> = when (this) {
        is Outcome.Ok -> Outcome.Ok(Page(value, null))
        is Outcome.Fail -> this
    }

    override suspend fun person(id: Int): Outcome<Person> {
        delay(PAUSE_MS)
        return when (val found = DemoSocial.person(id)) {
            is Outcome.Ok -> Outcome.Ok(found.value.copy(follow = DemoFeed.followInfo(id)))
            is Outcome.Fail -> found
        }
    }

    override suspend fun chats(page: Int): Outcome<Page<ChatInfo>> {
        delay(PAUSE_MS)
        return DemoSocial.chats().asPage()
    }

    override suspend fun chat(id: Int): Outcome<ChatInfo> {
        delay(PAUSE_MS)
        return DemoSocial.chat(id)
    }

    override suspend fun openChat(usernames: List<String>, title: String): Outcome<ChatInfo> {
        delay(PAUSE_MS)
        return DemoSocial.openChat(usernames, title)
    }

    // Без паузы: открытый чат опрашивается каждые несколько секунд
    override suspend fun messages(chatId: Int, afterId: Long?, changedSince: String?): Outcome<MessagesUpdate> =
        DemoSocial.messages(chatId, afterId, changedSince)

    override suspend fun olderMessages(chatId: Int, beforeId: Long): Outcome<Page<ChatMessage>> =
        DemoSocial.olderMessages()

    override suspend fun editMessage(chatId: Int, messageId: Long, text: String): Outcome<ChatMessage> =
        DemoSocial.editMessage(chatId, messageId, text)

    override suspend fun deleteMessage(chatId: Int, messageId: Long): Outcome<ChatMessage> =
        DemoSocial.deleteMessage(chatId, messageId)

    override suspend fun sendMessage(chatId: Int, text: String): Outcome<ChatMessage> =
        DemoSocial.send(chatId, text)

    override suspend fun leaveChat(chatId: Int): Outcome<String> {
        delay(PAUSE_MS)
        return DemoSocial.leaveChat(chatId)
    }

    override suspend fun communities(search: String, mineOnly: Boolean, page: Int): Outcome<Page<Community>> {
        delay(PAUSE_MS)
        return DemoSocial.communities(search, mineOnly).asPage()
    }

    override suspend fun community(id: Int): Outcome<Community> {
        delay(PAUSE_MS)
        return DemoSocial.community(id)
    }

    override suspend fun createCommunity(
        name: String,
        description: String,
        topic: String,
        isPrivate: Boolean,
    ): Outcome<Community> {
        delay(PAUSE_MS)
        return DemoSocial.create(name, description, topic, isPrivate)
    }

    override suspend fun communityAction(id: Int, action: String, userId: Int?, role: String?): Outcome<CommunityReply> {
        delay(PAUSE_MS)
        return DemoSocial.action(id, action, userId, role)
    }

    override suspend fun updateCommunity(
        id: Int,
        name: String,
        description: String,
        topic: String,
        isPrivate: Boolean,
    ): Outcome<Community> {
        delay(PAUSE_MS)
        return DemoSocial.update(id, name, description, topic, isPrivate)
    }

    override suspend fun deleteCommunity(id: Int): Outcome<String> {
        delay(PAUSE_MS)
        return DemoSocial.delete(id)
    }

    // ==================== ЛЕНТА ====================

    override suspend fun feed(scope: String, before: String?): Outcome<Page<Post>> {
        delay(PAUSE_MS)
        return DemoFeed.feed(scope)
    }

    override suspend fun personPosts(userId: Int, before: String?): Outcome<Page<Post>> {
        delay(PAUSE_MS)
        return DemoFeed.personPosts(userId)
    }

    override suspend fun post(id: Long): Outcome<Post> {
        delay(PAUSE_MS)
        return DemoFeed.post(id)
    }

    override suspend fun createPost(text: String, jpeg: ByteArray?, visibility: String): Outcome<PostResult> {
        delay(PAUSE_MS)
        val profile = (DemoSocial.profile() as? Outcome.Ok)?.value
        return DemoFeed.create(text, jpeg, visibility, profilePublic = profile?.isDiscoverable == true)
    }

    override suspend fun editPost(id: Long, text: String): Outcome<Post> {
        delay(PAUSE_MS)
        return DemoFeed.edit(id, text)
    }

    override suspend fun deletePost(id: Long): Outcome<String> {
        delay(PAUSE_MS)
        return DemoFeed.delete(id)
    }

    override suspend fun likePost(id: Long, liked: Boolean): Outcome<Post> = DemoFeed.like(id, liked)

    override suspend fun comments(postId: Long, before: String?): Outcome<Page<PostComment>> {
        delay(PAUSE_MS)
        return DemoFeed.comments(postId)
    }

    override suspend fun addComment(postId: Long, text: String): Outcome<PostComment> {
        delay(PAUSE_MS)
        return DemoFeed.addComment(postId, text)
    }

    override suspend fun deleteComment(postId: Long, commentId: Long): Outcome<String> {
        delay(PAUSE_MS)
        return DemoFeed.deleteComment(postId, commentId)
    }

    override suspend fun reportPost(
        postId: Long,
        commentId: Long?,
        reason: String,
        comment: String,
        alsoBlock: Boolean,
    ): Outcome<String> {
        delay(PAUSE_MS)
        return DemoFeed.report(postId, commentId, reason, alsoBlock)
    }

    override suspend fun follow(userId: Int, follow: Boolean): Outcome<FollowInfo> {
        delay(PAUSE_MS)
        return DemoFeed.follow(userId, follow)
    }

    override suspend fun postImage(id: Long): Outcome<ByteArray> = DemoFeed.image(id)

    // В демо-режиме сервера нет — и уведомлять некому
    override suspend fun registerDevice(token: String): Outcome<Unit> = Outcome.Ok(Unit)

    override suspend fun unregisterDevice(token: String): Outcome<Unit> = Outcome.Ok(Unit)

    override suspend fun uploadAvatar(jpeg: ByteArray): Outcome<SocialProfile> {
        delay(PAUSE_MS)
        return DemoSocial.uploadAvatar(jpeg)
    }

    override suspend fun removeAvatar(): Outcome<SocialProfile> {
        delay(PAUSE_MS)
        return DemoSocial.removeAvatar()
    }

    override suspend fun avatar(userId: Int): Outcome<ByteArray> = DemoSocial.avatar(userId)

    override suspend fun sendImage(chatId: Int, text: String, jpeg: ByteArray): Outcome<ChatMessage> {
        delay(PAUSE_MS)
        return DemoSocial.sendImage(chatId, text, jpeg)
    }

    override suspend fun messageImage(chatId: Int, messageId: Long): Outcome<ByteArray> =
        DemoSocial.messageImage(messageId)

    override suspend fun blocks(): Outcome<List<BlockedUser>> {
        delay(PAUSE_MS)
        return DemoSocial.blocks()
    }

    override suspend fun block(username: String): Outcome<String> {
        delay(PAUSE_MS)
        return DemoSocial.block(username)
    }

    override suspend fun unblock(userId: Int): Outcome<String> {
        delay(PAUSE_MS)
        return DemoSocial.unblock(userId)
    }

    override suspend fun reportMessage(
        chatId: Int,
        messageId: Long,
        reason: String,
        comment: String,
        alsoBlock: Boolean,
    ): Outcome<String> {
        delay(PAUSE_MS)
        return DemoSocial.report(chatId, messageId, reason, alsoBlock)
    }

    // ==================== КОШЕЛЁК ====================
    // Демо-режим ведёт себя как тестовый режим сервера: пополнение сразу,
    // без страницы оплаты. Лимиты условные.

    private val withdrawals = mutableListOf<Withdrawal>()
    private var nextWithdrawalId = 1
    private var verificationSubmitted = false
    private val documents = mutableListOf<KycDocument>()

    override suspend fun wallet(): Outcome<WalletInfo> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            WalletInfo(
                balance = money(balance),
                topupMin = "1.00", topupMax = "100000.00",
                topupMethods = listOf(Choice("card", "Банковская карта"), Choice("sbp", "СБП"), Choice("yoomoney", "ЮMoney")),
                topupSimulated = true,
                withdrawMin = "500.00", withdrawMax = "15000.00",
                withdrawMethods = listOf(Choice("card", "Банковская карта"), Choice("sbp", "СБП"), Choice("yoomoney", "ЮMoney")),
                sbpBanks = listOf(Choice("100000000004", "Т-Банк"), Choice("100000000111", "Сбербанк")),
                verificationLevel = "basic",
            )
        )
    }

    override suspend fun topUp(amount: String, method: String): Outcome<TopUpResult> {
        delay(PAUSE_MS)
        val value = amount.replace(',', '.').toDoubleOrNull() ?: return Outcome.Fail("Сумма пополнения указана некорректно")
        if (value < 1) return Outcome.Fail("Минимальная сумма пополнения — 1 ₽")
        balance += value
        return Outcome.Ok(TopUpResult(true, "Баланс пополнен на ${money(value)} ₽ (демо-режим)", null))
    }

    override suspend fun withdrawals(): Outcome<List<Withdrawal>> {
        delay(PAUSE_MS)
        return Outcome.Ok(withdrawals.toList())
    }

    override suspend fun createWithdrawal(fields: Map<String, String>): Outcome<String> {
        delay(PAUSE_MS)
        val value = fields["amount"].orEmpty().replace(',', '.').toDoubleOrNull()
            ?: return Outcome.Fail("Введите сумму")
        if (value < 500) return Outcome.Fail("Минимальная сумма вывода — 500 ₽")
        if (value > 15000) return Outcome.Fail("Превышен лимит вывода для вашего уровня верификации: 15000 ₽")
        if (value > balance) return Outcome.Fail("Недостаточно средств на балансе")
        val method = fields["payment_method"].orEmpty()
        val details = when (method) {
            "card" -> "****" + fields["card_number"].orEmpty().takeLast(4)
            "sbp" -> "+7 *** ***-" + fields["phone_number"].orEmpty().takeLast(2)
            else -> "***" + fields["wallet_number"].orEmpty().takeLast(4)
        }
        balance -= value
        withdrawals.add(0, Withdrawal(nextWithdrawalId++, money(value),
            mapOf("card" to "Банковская карта", "sbp" to "СБП").getOrDefault(method, "ЮMoney"),
            details, "pending", "На рассмотрении", "только что", true))
        return Outcome.Ok("Заявка на вывод ${money(value)} ₽ создана (демо-режим)")
    }

    override suspend fun cancelWithdrawal(id: Int): Outcome<String> {
        delay(PAUSE_MS)
        val index = withdrawals.indexOfFirst { it.id == id && it.canCancel }
        if (index < 0) return Outcome.Fail("Невозможно отменить заявку в текущем статусе")
        val withdrawal = withdrawals[index]
        balance += withdrawal.amount.toDouble()
        withdrawals[index] = withdrawal.copy(status = "cancelled", statusTitle = "Отменена", canCancel = false)
        return Outcome.Ok("Заявка #$id отменена, средства возвращены на баланс")
    }

    override suspend fun verification(): Outcome<VerificationInfo> {
        delay(PAUSE_MS)
        return Outcome.Ok(
            VerificationInfo(
                level = "basic", levelTitle = "Базовая верификация",
                submitted = verificationSubmitted,
                passportMasked = if (verificationSubmitted) "45** ***456" else "—",
                limits = mapOf("single_withdrawal" to "15000.00", "daily_withdrawal" to "50000.00"),
                documents = documents.toList(),
                documentTypes = listOf(Choice("passport", "Паспорт РФ"), Choice("driver_license", "Водительское удостоверение"),
                    Choice("snils", "СНИЛС"), Choice("inn", "ИНН")),
            )
        )
    }

    override suspend fun submitVerification(
        fullName: String,
        birthDate: String,
        series: String,
        number: String,
    ): Outcome<String> {
        delay(PAUSE_MS)
        if (fullName.trim().split(" ").size < 2) return Outcome.Fail("Укажите фамилию и имя полностью")
        if (series.length != 4 || number.length != 6) return Outcome.Fail("Серия — 4 цифры, номер — 6 цифр")
        val year = birthDate.takeLast(4).toIntOrNull() ?: birthDate.take(4).toIntOrNull()
            ?: return Outcome.Fail("Укажите дату рождения в формате ДД.ММ.ГГГГ")
        if (2026 - year < 18) return Outcome.Fail("Сервис доступен только совершеннолетним")
        verificationSubmitted = true
        return Outcome.Ok("Данные отправлены на проверку. Загрузите скан документа (демо-режим)")
    }

    override suspend fun uploadDocument(type: String, jpeg: ByteArray): Outcome<String> {
        delay(PAUSE_MS)
        documents.add(0, KycDocument(documents.size + 1, if (type == "passport") "Паспорт РФ" else type,
            "На проверке", "только что"))
        return Outcome.Ok("Документ загружен на проверку (демо-режим)")
    }

    override suspend fun resetPassword(email: String): Outcome<String> {
        delay(PAUSE_MS)
        if (!email.contains("@")) return Outcome.Fail("Введите адрес почты")
        return Outcome.Ok("Если адрес $email зарегистрирован, на него отправлено письмо со ссылкой (демо-режим)")
    }
}
