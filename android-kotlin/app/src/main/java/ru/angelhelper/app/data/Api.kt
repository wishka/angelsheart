package ru.angelhelper.app.data

/**
 * Всё, что приложение просит у сервера.
 *
 * Интерфейс один, реализаций две: [HttpApi] ходит на настоящий Django,
 * [DemoApi] отдаёт выдуманные данные из памяти. Экраны не знают, с какой
 * из них работают, — поэтому демо-режим не требует ни одной особой ветки
 * в разметке и не может разойтись с боевым поведением.
 *
 * Часть методов помечена как заглушка: в серверном API этих точек нет
 * вовсе. Они отвечают выдуманным успехом и честно пишут об этом на
 * экране — приложение делается для тестирования, и притворяться, будто
 * деньги пополнились, оно не должно.
 */
interface Api {

    suspend fun login(username: String, password: String, code: String = ""): Outcome<Tokens>

    /**
     * Выход с отзывом refresh-токена на сервере.
     *
     * Только стереть токены на телефоне мало: refresh остаётся
     * действительным ещё неделю, и с потерянного телефона по нему
     * по-прежнему можно войти.
     */
    suspend fun logout(): Outcome<Unit>

    /**
     * Регистрация сразу возвращает токены: сервер их выдаёт, и заставлять
     * человека набирать тот же пароль второй раз на экране входа незачем
     * — тем более что вход ограничен десятью попытками в час.
     */
    suspend fun register(
        username: String,
        email: String,
        password: String,
        acceptTerms: Boolean,
        consentDataProcessing: Boolean,
    ): Outcome<Tokens>

    suspend fun me(): Outcome<Profile>

    suspend fun dashboard(): Outcome<Dashboard>

    /** Страницы по номеру: page = 1, 2, … ; Page.next — номер следующей. */
    suspend fun fundraises(search: String = "", page: Int = 1): Outcome<Page<Fundraise>>

    suspend fun fundraise(id: Int): Outcome<Fundraise>

    suspend fun donate(
        id: Int,
        amount: String,
        message: String,
        anonymous: Boolean,
    ): Outcome<String>

    suspend fun transfer(receiver: String, amount: String, comment: String): Outcome<String>

    suspend fun transactions(page: Int = 1): Outcome<Page<Tx>>

    suspend fun leaders(): Outcome<List<Leader>>

    suspend fun consents(): Outcome<List<Consent>>

    // ==================== СООБЩЕСТВО ====================

    /** Справочник интересов для анкеты, фильтра и темы группы. */
    suspend fun interests(): Outcome<List<Interest>>

    suspend fun socialProfile(): Outcome<SocialProfile>

    /**
     * Сохранение анкеты. Включение isDiscoverable на сервере фиксирует
     * согласие на распространение ПДн — экран предупреждает об этом.
     */
    suspend fun saveSocialProfile(profile: SocialProfile): Outcome<SocialProfile>

    /** Поиск людей. Находятся только те, кто сам открыл анкету для поиска. */
    suspend fun people(filter: PeopleFilter, page: Int = 1): Outcome<Page<Person>>

    /** Карточка с подписками (Person.follow). */
    suspend fun person(id: Int): Outcome<Person>

    suspend fun chats(page: Int = 1): Outcome<Page<ChatInfo>>

    suspend fun chat(id: Int): Outcome<ChatInfo>

    /**
     * Открыть чат. Один получатель без названия — личный чат (существующий
     * возвращается тот же), несколько — групповой, название обязательно.
     * Получатели — точные имена пользователей, как при переводе.
     */
    suspend fun openChat(usernames: List<String>, title: String): Outcome<ChatInfo>

    /**
     * Сообщения чата по возрастанию. afterId == null — последние 50;
     * иначе только новые после него (так опрашивается открытый чат).
     */
    suspend fun messages(chatId: Int, afterId: Long?, changedSince: String? = null): Outcome<MessagesUpdate>

    /** Более ранние сообщения — до beforeId; Page.next — курсор для следующих. */
    suspend fun olderMessages(chatId: Int, beforeId: Long): Outcome<Page<ChatMessage>>

    /** Исправить своё сообщение. */
    suspend fun editMessage(chatId: Int, messageId: Long, text: String): Outcome<ChatMessage>

    /** Удалить своё сообщение у всех: в чате останется «Сообщение удалено». */
    suspend fun deleteMessage(chatId: Int, messageId: Long): Outcome<ChatMessage>

    suspend fun sendMessage(chatId: Int, text: String): Outcome<ChatMessage>

    suspend fun leaveChat(chatId: Int): Outcome<String>

    suspend fun communities(search: String, mineOnly: Boolean, page: Int = 1): Outcome<Page<Community>>

    suspend fun community(id: Int): Outcome<Community>

    suspend fun createCommunity(
        name: String,
        description: String,
        topic: String,
        isPrivate: Boolean,
    ): Outcome<Community>

    /**
     * action: join, leave, approve, decline, role (с role = admin|member),
     * transfer (передать владение), remove (исключить). Кроме join и
     * leave нужен userId.
     */
    suspend fun communityAction(
        id: Int,
        action: String,
        userId: Int? = null,
        role: String? = null,
    ): Outcome<CommunityReply>

    suspend fun updateCommunity(
        id: Int,
        name: String,
        description: String,
        topic: String,
        isPrivate: Boolean,
    ): Outcome<Community>

    suspend fun deleteCommunity(id: Int): Outcome<String>

    // ==================== ЛЕНТА ====================

    /** scope: following — свои и подписки; all — публичные всех. Курсор — Page.next. */
    suspend fun feed(scope: String, before: String? = null): Outcome<Page<Post>>

    suspend fun personPosts(userId: Int, before: String? = null): Outcome<Page<Post>>

    suspend fun post(id: Long): Outcome<Post>

    /** visibility: public | followers. jpeg — уже уменьшенное фото или null. */
    suspend fun createPost(text: String, jpeg: ByteArray?, visibility: String): Outcome<PostResult>

    suspend fun editPost(id: Long, text: String): Outcome<Post>

    suspend fun deletePost(id: Long): Outcome<String>

    suspend fun likePost(id: Long, liked: Boolean): Outcome<Post>

    suspend fun comments(postId: Long, before: String? = null): Outcome<Page<PostComment>>

    suspend fun addComment(postId: Long, text: String): Outcome<PostComment>

    suspend fun deleteComment(postId: Long, commentId: Long): Outcome<String>

    /** Жалоба на публикацию (commentId == null) или на комментарий к ней. */
    suspend fun reportPost(
        postId: Long,
        commentId: Long?,
        reason: String,
        comment: String,
        alsoBlock: Boolean,
    ): Outcome<String>

    suspend fun follow(userId: Int, follow: Boolean): Outcome<FollowInfo>

    suspend fun postImage(id: Long): Outcome<ByteArray>

    // ==================== УВЕДОМЛЕНИЯ ====================

    /** Телефон начинает получать push для этой учётной записи. */
    suspend fun registerDevice(token: String): Outcome<Unit>

    /** При выходе: уведомления этой учётной записи сюда больше не идут. */
    suspend fun unregisterDevice(token: String): Outcome<Unit>

    // ==================== ФОТО ====================

    /**
     * Фото анкеты. jpeg — уже уменьшенный на телефоне снимок
     * (ImageTools.prepareJpeg); сервер всё равно пересохраняет его и
     * срезает метаданные.
     */
    suspend fun uploadAvatar(jpeg: ByteArray): Outcome<SocialProfile>

    suspend fun removeAvatar(): Outcome<SocialProfile>

    /** Фото человека; сервер отдаёт его только тем, кому оно видно. */
    suspend fun avatar(userId: Int): Outcome<ByteArray>

    /** Сообщение с фото; text может быть пустым. */
    suspend fun sendImage(chatId: Int, text: String, jpeg: ByteArray): Outcome<ChatMessage>

    suspend fun messageImage(chatId: Int, messageId: Long): Outcome<ByteArray>

    // ==================== ЧЁРНЫЙ СПИСОК И ЖАЛОБЫ ====================

    suspend fun blocks(): Outcome<List<BlockedUser>>

    /**
     * В чёрный список. Действует в обе стороны: личная переписка
     * закрывается для обоих, в поиске друг друга не видно, в общих
     * чатах сообщения заблокированного скрыты.
     */
    suspend fun block(username: String): Outcome<String>

    suspend fun unblock(userId: Int): Outcome<String>

    /**
     * Жалоба на сообщение модератору. reason — ключ из REPORT_REASONS;
     * alsoBlock сразу добавляет автора в чёрный список.
     */
    suspend fun reportMessage(
        chatId: Int,
        messageId: Long,
        reason: String,
        comment: String,
        alsoBlock: Boolean,
    ): Outcome<String>

    // ==================== КОШЕЛЁК ====================
    // Правила — на сервере (main/wallet.py), общие с сайтом.

    suspend fun wallet(): Outcome<WalletInfo>

    /**
     * Пополнение. Обычно ответ — адрес страницы оплаты ЮKassa (открыть в
     * браузере); в тестовом режиме сервера деньги зачисляются сразу.
     */
    suspend fun topUp(amount: String, method: String): Outcome<TopUpResult>

    suspend fun withdrawals(): Outcome<List<Withdrawal>>

    /**
     * Заявка на вывод. fields — как у формы сайта: payment_method, amount,
     * card_number/card_holder/expiry_date, phone_number/bank_id или
     * wallet_number.
     */
    suspend fun createWithdrawal(fields: Map<String, String>): Outcome<String>

    suspend fun cancelWithdrawal(id: Int): Outcome<String>

    suspend fun verification(): Outcome<VerificationInfo>

    /** Паспортные данные — на проверку администратору; уровень сам не повышается. */
    suspend fun submitVerification(
        fullName: String,
        birthDate: String,
        series: String,
        number: String,
    ): Outcome<String>

    /** Скан документа (JPEG) — на проверку. */
    suspend fun uploadDocument(type: String, jpeg: ByteArray): Outcome<String>

    /** Письмо со ссылкой; новый пароль задаётся по ссылке в браузере. */
    suspend fun resetPassword(email: String): Outcome<String>
}

/**
 * Признак того, что вход больше не действует.
 *
 * Передаётся текстом сообщения, а не отдельным типом: экранам он не
 * нужен, его узнаёт только модель — и сразу возвращает человека на
 * экран входа. Иначе приложение считает его вошедшим и показывает 401
 * на каждом экране, а уйти оттуда некуда.
 */
const val SESSION_EXPIRED: String = "__session_expired__"

/** Сервер требует код двухфакторной проверки (отвечает 403). */
const val TWO_FACTOR_REQUIRED: String = "__two_factor_required__"
