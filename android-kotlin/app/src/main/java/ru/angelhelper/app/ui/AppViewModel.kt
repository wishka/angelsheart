package ru.angelhelper.app.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.update
import ru.angelhelper.app.data.Api
import ru.angelhelper.app.push.Push
import ru.angelhelper.app.data.BlockedUser
import ru.angelhelper.app.data.ChatInfo
import ru.angelhelper.app.data.ChatMessage
import ru.angelhelper.app.data.Community
import ru.angelhelper.app.data.Interest
import ru.angelhelper.app.data.PeopleFilter
import ru.angelhelper.app.data.Person
import ru.angelhelper.app.data.SocialProfile
import ru.angelhelper.app.data.AppPrefs
import ru.angelhelper.app.data.Consent
import ru.angelhelper.app.data.Dashboard
import ru.angelhelper.app.data.DemoApi
import ru.angelhelper.app.data.Fundraise
import ru.angelhelper.app.data.HttpApi
import ru.angelhelper.app.data.Leader
import ru.angelhelper.app.data.Outcome
import ru.angelhelper.app.data.Profile
import ru.angelhelper.app.data.SESSION_EXPIRED
import ru.angelhelper.app.data.TWO_FACTOR_REQUIRED
import ru.angelhelper.app.data.Tx
import ru.angelhelper.app.data.FollowInfo
import ru.angelhelper.app.data.MessagesUpdate
import ru.angelhelper.app.data.Page
import ru.angelhelper.app.data.Post
import ru.angelhelper.app.data.PostComment
import ru.angelhelper.app.data.PostResult
import ru.angelhelper.app.data.TopUpResult
import ru.angelhelper.app.data.VerificationInfo
import ru.angelhelper.app.data.WalletInfo
import ru.angelhelper.app.data.Withdrawal
import ru.angelhelper.app.data.appendPage

/**
 * Экраны приложения.
 *
 * Свой стек вместо Navigation Compose: экранов немного, а библиотека —
 * лишняя зависимость с меняющимся от версии к версии API. Здесь переход
 * виден целиком и не требует ни строковых маршрутов, ни разбора
 * аргументов из адреса.
 */
sealed interface Screen {
    data object Login : Screen
    data object Register : Screen
    data object PasswordReset : Screen
    /** Кошелёк: баланс, оборот и последние операции. */
    data object Dashboard : Screen
    data object Fundraises : Screen
    data class FundraiseDetail(val id: Int) : Screen
    data object Transfer : Screen
    data object History : Screen
    data object Profile : Screen
    data object Verification : Screen
    data object TopUp : Screen
    data object Withdraw : Screen
    data object Leaders : Screen
    data object Consents : Screen
    data object Settings : Screen

    // Лента
    data object Feed : Screen
    data class PostDetail(val id: Long) : Screen
    data object NewPost : Screen

    // Сообщество
    data object People : Screen
    data class PersonDetail(val id: Int) : Screen
    data object Groups : Screen
    data class GroupDetail(val id: Int) : Screen
    data object NewGroup : Screen
    data class GroupEdit(val id: Int) : Screen
    data object Chats : Screen
    data class Chat(val id: Int) : Screen
    data object NewChat : Screen
    data object SocialProfileEdit : Screen
    data object BlockList : Screen
}

/**
 * Всё, что видно на экранах.
 *
 * Одно состояние на приложение, а не по одному на экран: экранов мало,
 * они делят баланс и профиль, и разнесённое состояние означало бы, что
 * после перевода баланс на главной остаётся старым.
 */
/**
 * Сообщение человеку.
 *
 * Со счётчиком, а не просто текстом: показ идёт через LaunchedEffect,
 * который срабатывает на изменение ключа. Без счётчика два одинаковых
 * сообщения подряд («Недостаточно средств») показывались бы один раз.
 */
data class Message(val id: Long, val text: String, val isError: Boolean)

data class UiState(
    val stack: List<Screen> = listOf(Screen.Login),
    val busy: Boolean = false,
    val message: Message? = null,
    val profile: Profile? = null,
    val dashboard: Dashboard? = null,
    val fundraises: List<Fundraise> = emptyList(),
    val fundraisesNext: String? = null,
    val fundraisesSearch: String = "",
    val openFundraise: Fundraise? = null,
    val transactions: List<Tx> = emptyList(),
    val transactionsNext: String? = null,
    val leaders: List<Leader> = emptyList(),
    val consents: List<Consent> = emptyList(),
    val demoMode: Boolean = false,
    val baseUrl: String = AppPrefs.DEFAULT_BASE_URL,
    /** Сервер попросил код двухфакторной проверки: экран входа раскрывает поле. */
    val twoFactorRequired: Boolean = false,

    // ---- Лента ----
    /** following — свои и подписки, all — публичные всех. */
    val feedScope: String = "following",
    val feed: List<Post> = emptyList(),
    val feedNext: String? = null,
    val openPost: Post? = null,
    val comments: List<PostComment> = emptyList(),
    val commentsNext: String? = null,
    val personPosts: List<Post> = emptyList(),
    val personPostsNext: String? = null,

    // ---- Кошелёк ----
    val wallet: WalletInfo? = null,
    val withdrawals: List<Withdrawal> = emptyList(),
    val verification: VerificationInfo? = null,
    /** Страница оплаты ЮKassa: MainActivity открывает её в браузере и сбрасывает. */
    val openUrl: String? = null,

    // ---- Сообщество ----
    val interests: List<Interest> = emptyList(),
    val peopleFilter: PeopleFilter = PeopleFilter(),
    val people: List<Person> = emptyList(),
    val peopleNext: String? = null,
    val openPerson: Person? = null,
    val socialProfile: SocialProfile? = null,
    val chats: List<ChatInfo> = emptyList(),
    val chatsNext: String? = null,
    val openChat: ChatInfo? = null,
    val chatMessages: List<ChatMessage> = emptyList(),
    /** В открытом чате есть сообщения старше загруженных. */
    val chatHasOlder: Boolean = false,
    val groups: List<Community> = emptyList(),
    val groupsNext: String? = null,
    val groupsSearch: String = "",
    val groupsMineOnly: Boolean = false,
    val openGroup: Community? = null,
    /** Получатель, подставляемый в форму перевода из анкеты человека. */
    val transferPrefill: String = "",
    val blocks: List<BlockedUser> = emptyList(),
) {
    val screen: Screen get() = stack.last()
    val canGoBack: Boolean get() = stack.size > 1
}

class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val prefs = AppPrefs(application)

    private val _state = MutableStateFlow(
        UiState(
            stack = listOf(if (prefs.signedIn) Screen.Feed else Screen.Login),
            demoMode = prefs.demoMode,
            baseUrl = prefs.baseUrl,
        )
    )
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        // Загрузка при запуске. Раньше её не было вовсе: приложение с
        // сохранённым токеном открывалось на «Главной» с пустым балансом
        // и надписью «операций нет», пока человек не нажмёт «Обновить».
        loadFor(_state.value.screen)
        // Токен push мог смениться, пока приложение было закрыто
        if (prefs.signedIn) registerPush()
    }

    // ==================== УВЕДОМЛЕНИЯ ====================

    /**
     * Сообщить серверу токен этого телефона. Без Firebase в сборке и в
     * демо-режиме ничего не делает. Ошибки молча пропускаются: без push
     * приложение работает, и всплывающее «не удалось включить
     * уведомления» при каждом запуске только мешало бы.
     */
    private fun registerPush() {
        if (prefs.demoMode) return
        Push.fetchToken(getApplication<Application>()) { token ->
            prefs.pushToken = token
            viewModelScope.launch { api().registerDevice(token) }
        }
    }

    /** Нажатие на уведомление: открыть чат поверх списка чатов. */
    fun openChatFromNotification(chatId: Int) {
        if (!prefs.signedIn) return
        _state.update { it.copy(stack = listOf(Screen.Chats), message = null) }
        go(Screen.Chat(chatId))
    }

    val notificationsAsked: Boolean get() = prefs.notificationsAsked

    fun markNotificationsAsked() {
        prefs.notificationsAsked = true
    }

    /**
     * Какой реализацией пользоваться, решается при каждом обращении.
     *
     * Не полем: переключатель демо-режима должен действовать сразу, а
     * сохранённая один раз реализация продолжала бы ходить в сеть до
     * перезапуска приложения.
     */
    private fun api(): Api = if (prefs.demoMode) DemoApi else HttpApi(prefs)

    // ==================== ПЕРЕХОДЫ ====================

    fun go(screen: Screen) {
        _state.value = _state.value.copy(
            stack = _state.value.stack + screen,
            message = null,
        )
        loadFor(screen)
    }

    /** Переход в корень: после входа и выхода стек не должен копиться. */
    fun goRoot(screen: Screen) {
        _state.value = _state.value.copy(stack = listOf(screen), message = null)
        loadFor(screen)
    }

    fun back() {
        val stack = _state.value.stack
        if (stack.size <= 1) return
        _state.value = _state.value.copy(
            stack = stack.dropLast(1),
            message = null,
        )
        loadFor(_state.value.stack.last())
    }

    private var messageCounter = 0L

    private fun say(text: String, isError: Boolean) {
        messageCounter += 1
        _state.value = _state.value.copy(
            message = Message(messageCounter, text, isError),
        )
    }

    fun dismissMessage() {
        _state.value = _state.value.copy(message = null)
    }

    private fun loadFor(screen: Screen) {
        when (screen) {
            Screen.Feed -> loadFeed(_state.value.feedScope)
            is Screen.PostDetail -> loadPost(screen.id)
            Screen.Dashboard -> {
                refreshDashboard()
                loadWallet()
            }
            Screen.TopUp -> loadWallet()
            Screen.Withdraw -> {
                loadWallet()
                loadWithdrawals()
            }
            Screen.Verification -> {
                loadProfile()
                loadVerification()
            }
            Screen.Fundraises -> loadFundraises(_state.value.fundraisesSearch)
            is Screen.FundraiseDetail -> loadFundraise(screen.id)
            Screen.History -> loadTransactions()
            Screen.Profile -> {
                loadProfile()
                loadSocialProfile()
            }
            Screen.Leaders -> loadLeaders()
            Screen.Consents -> loadConsents()
            Screen.People -> {
                ensureInterests()
                searchPeople(_state.value.peopleFilter)
            }
            is Screen.PersonDetail -> loadPerson(screen.id)
            Screen.Groups -> loadGroups(_state.value.groupsSearch, _state.value.groupsMineOnly)
            is Screen.GroupDetail -> loadGroup(screen.id)
            Screen.NewGroup -> ensureInterests()
            is Screen.GroupEdit -> {
                ensureInterests()
                if (_state.value.openGroup?.id != screen.id) loadGroup(screen.id)
            }
            Screen.Chats -> loadChats()
            is Screen.Chat -> openChatThread(screen.id)
            Screen.BlockList -> loadBlocks()
            Screen.SocialProfileEdit -> {
                ensureInterests()
                loadSocialProfile()
            }
            else -> Unit
        }
    }

    // ==================== ОБЩАЯ ОБЁРТКА ====================

    /**
     * Выполняет запрос и раскладывает результат.
     *
     * Единственное место, где взводится и снимается признак ожидания:
     * иначе экран, у которого забыли снять busy в ветке ошибки, остаётся
     * навсегда с крутящимся кружком.
     */
    private fun <T> run(
        onOk: (T) -> Unit = {},
        block: suspend (Api) -> Outcome<T>,
    ) {
        viewModelScope.launch {
            // Сообщение здесь НЕ сбрасывается намеренно. viewModelScope
            // работает на главном потоке «немедленно», поэтому вложенный
            // запуск (перевод → обновление главной) начинался синхронно и
            // затирал только что поставленное сообщение до того, как его
            // успевал заметить экран: человек нажимал «Перевести», поле
            // очищалось — и ни слова о том, ушли деньги или нет.
            _state.value = _state.value.copy(busy = true)
            when (val outcome = block(api())) {
                is Outcome.Ok -> {
                    _state.value = _state.value.copy(busy = false)
                    onOk(outcome.value)
                }
                is Outcome.Fail -> {
                    _state.value = _state.value.copy(busy = false)
                    handleFailure(outcome.message)
                }
            }
        }
    }

    /**
     * Разбор отказа.
     *
     * Два ответа означают не ошибку, а смену состояния: просроченный
     * вход возвращает на экран входа, требование кода двухфакторной
     * проверки раскрывает поле кода. Показывать их как текст ошибки
     * означало бы оставить человека на экране, с которого некуда идти.
     */
    private fun handleFailure(message: String) {
        when (message) {
            SESSION_EXPIRED -> {
                prefs.clearTokens()
                _state.value = UiState(
                    stack = listOf(Screen.Login),
                    demoMode = prefs.demoMode,
                    baseUrl = prefs.baseUrl,
                )
                say("Вход больше не действует. Войдите заново.", isError = true)
            }
            TWO_FACTOR_REQUIRED -> {
                _state.value = _state.value.copy(twoFactorRequired = true)
                say("Введите код двухфакторной проверки", isError = true)
            }
            else -> say(message, isError = true)
        }
    }

    // ==================== ВХОД ====================

    fun login(username: String, password: String, code: String) = run(
        onOk = { tokens ->
            prefs.saveTokens(tokens)
            registerPush()
            if (prefs.username.isBlank()) prefs.username = username
            goRoot(Screen.Feed)
        },
    ) { it.login(username, password, code) }

    fun register(username: String, email: String, password: String, consents: Boolean) = run(
        onOk = { tokens ->
            // Сервер выдал токены при регистрации — входим сразу, не
            // отправляя человека набирать тот же пароль второй раз
            prefs.saveTokens(tokens)
            registerPush()
            if (prefs.username.isBlank()) prefs.username = username
            goRoot(Screen.Feed)
            say(
                "Учётная запись создана. Проверьте почту — без подтверждения " +
                    "адреса недоступны вывод средств и публикация сбора.",
                isError = false,
            )
        },
    ) { it.register(username, email, password, consents, consents) }

    fun resetPassword(email: String) = run(
        onOk = { text -> say(text, isError = false) },
    ) { it.resetPassword(email) }

    /**
     * Выход.
     *
     * Токены стираются сразу, а отзыв refresh на сервере уходит следом и
     * его результат ни на что не влияет: неудача отзыва — не повод
     * оставить человека внутри приложения.
     */
    fun logout() {
        viewModelScope.launch {
            val api = api()
            try {
                // Сначала отвязать телефон: после выхода чужие уведомления
                // сюда приходить не должны, а без токена этого уже не сделать
                prefs.pushToken.takeIf { it.isNotBlank() }?.let { api.unregisterDevice(it) }
                api.logout()
            } catch (_: Exception) {
                // отзыв не удался — выходим всё равно
            }
            prefs.clearTokens()
            _state.value = UiState(
                stack = listOf(Screen.Login),
                demoMode = prefs.demoMode,
                baseUrl = prefs.baseUrl,
            )
        }
    }

    // ==================== ДАННЫЕ ====================

    fun refreshDashboard() = run(
        onOk = { data -> _state.value = _state.value.copy(dashboard = data) },
    ) { it.dashboard() }

    fun loadProfile() = run(
        onOk = { profile -> _state.value = _state.value.copy(profile = profile) },
    ) { it.me() }

    fun loadFundraises(search: String) {
        _state.update { it.copy(fundraisesSearch = search) }
        run(onOk = { page: Page<Fundraise> ->
            _state.update { it.copy(fundraises = page.items, fundraisesNext = page.next) }
        }) { it.fundraises(search) }
    }

    fun moreFundraises() {
        val s = _state.value
        val next = s.fundraisesNext ?: return
        run(onOk = { page: Page<Fundraise> ->
            _state.update {
                it.copy(fundraises = it.fundraises.appendPage(page.items) { f -> f.id }, fundraisesNext = page.next)
            }
        }) { it.fundraises(s.fundraisesSearch, pageNumber(next)) }
    }

    fun loadFundraise(id: Int) = run(
        onOk = { item -> _state.value = _state.value.copy(openFundraise = item) },
    ) { it.fundraise(id) }

    fun loadTransactions() = run(
        onOk = { page: Page<Tx> ->
            _state.update { it.copy(transactions = page.items, transactionsNext = page.next) }
        },
    ) { it.transactions() }

    fun moreTransactions() {
        val next = _state.value.transactionsNext ?: return
        run(onOk = { page: Page<Tx> ->
            _state.update {
                it.copy(transactions = it.transactions.appendPage(page.items) { tx -> tx.id }, transactionsNext = page.next)
            }
        }) { it.transactions(pageNumber(next)) }
    }

    /** Номер следующей страницы из Page.next. */
    private fun pageNumber(next: String): Int = next.toIntOrNull() ?: 1

    fun loadLeaders() = run(
        onOk = { list -> _state.value = _state.value.copy(leaders = list) },
    ) { it.leaders() }

    fun loadConsents() = run(
        onOk = { list -> _state.value = _state.value.copy(consents = list) },
    ) { it.consents() }

    // ==================== ОПЕРАЦИИ ====================

    fun donate(id: Int, amount: String, message: String, anonymous: Boolean) = run(
        onOk = { text ->
            say(text, isError = false)
            loadFundraise(id)
        },
    ) { it.donate(id, amount, message, anonymous) }

    fun transfer(receiver: String, amount: String, comment: String) = run(
        onOk = { text ->
            say(text, isError = false)
            refreshDashboard()
        },
    ) { it.transfer(receiver, amount, comment) }

    // ==================== КОШЕЛЁК ====================

    fun loadWallet() = run(
        onOk = { info: WalletInfo -> _state.update { it.copy(wallet = info) } },
    ) { it.wallet() }

    /**
     * Пополнение. Обычный ответ — страница оплаты ЮKassa: она открывается
     * в браузере, а деньги придут по вебхуку. Баланс перечитывается при
     * возвращении в «Кошелёк». В тестовом режиме сервера зачисление сразу.
     */
    fun topUp(amount: String, method: String) = run(
        onOk = { result: TopUpResult ->
            say(result.message, isError = false)
            val url = result.confirmationUrl
            if (url != null) {
                _state.update { it.copy(openUrl = url) }
            } else {
                refreshDashboard()
                loadWallet()
            }
        },
    ) { it.topUp(amount, method) }

    fun consumeOpenUrl() {
        _state.update { it.copy(openUrl = null) }
    }

    fun loadWithdrawals() = run(
        onOk = { list: List<Withdrawal> -> _state.update { it.copy(withdrawals = list) } },
    ) { it.withdrawals() }

    /** fields — как у формы сайта; правила и лимиты проверяет сервер. */
    fun createWithdrawal(fields: Map<String, String>, onDone: () -> Unit = {}) = run(
        onOk = { text: String ->
            say(text, isError = false)
            onDone()
            loadWithdrawals()
            loadWallet()
        },
    ) { it.createWithdrawal(fields) }

    fun cancelWithdrawal(id: Int) = run(
        onOk = { text: String ->
            say(text, isError = false)
            loadWithdrawals()
            loadWallet()
        },
    ) { it.cancelWithdrawal(id) }

    fun loadVerification() = run(
        onOk = { info: VerificationInfo -> _state.update { it.copy(verification = info) } },
    ) { it.verification() }

    fun submitVerification(fullName: String, birthDate: String, series: String, number: String) =
        run(
            onOk = { text: String ->
                say(text, isError = false)
                loadVerification()
            },
        ) { it.submitVerification(fullName, birthDate, series, number) }

    /** Скан документа: крупнее аватара, чтобы читались буквы. */
    fun uploadDocument(type: String, uri: Uri) {
        viewModelScope.launch {
            val jpeg = readPhoto(uri, 2000)
            if (jpeg == null) {
                say("Не удалось прочитать фото", isError = true)
                return@launch
            }
            run(onOk = { text: String ->
                say(text, isError = false)
                loadVerification()
            }) { it.uploadDocument(type, jpeg) }
        }
    }

    // ==================== ЛЕНТА ====================

    fun loadFeed(scope: String) {
        if (scope != _state.value.feedScope) {
            _state.update { it.copy(feedScope = scope, feed = emptyList(), feedNext = null) }
        }
        run(onOk = { page: Page<Post> ->
            // Пока шёл запрос, человек мог переключить вкладку
            if (_state.value.feedScope == scope) {
                _state.update { it.copy(feed = page.items, feedNext = page.next) }
            }
        }) { it.feed(scope) }
    }

    fun moreFeed() {
        val s = _state.value
        val next = s.feedNext ?: return
        run(onOk = { page: Page<Post> ->
            if (_state.value.feedScope == s.feedScope) {
                _state.update { it.copy(feed = it.feed.appendPage(page.items) { p -> p.id }, feedNext = page.next) }
            }
        }) { it.feed(s.feedScope, next) }
    }

    fun morePersonPosts(userId: Int) {
        val next = _state.value.personPostsNext ?: return
        run(onOk = { page: Page<Post> ->
            _state.update {
                it.copy(personPosts = it.personPosts.appendPage(page.items) { p -> p.id }, personPostsNext = page.next)
            }
        }) { it.personPosts(userId, next) }
    }

    fun loadPost(id: Long) {
        if (_state.value.openPost?.id != id) {
            _state.update { it.copy(openPost = null, comments = emptyList(), commentsNext = null) }
        }
        run(onOk = { post: Post -> _state.update { it.copy(openPost = post) } }) { it.post(id) }
        run(onOk = { page: Page<PostComment> ->
            _state.update { it.copy(comments = page.items, commentsNext = page.next) }
        }) { it.comments(id) }
    }

    fun moreComments(postId: Long) {
        val next = _state.value.commentsNext ?: return
        run(onOk = { page: Page<PostComment> ->
            _state.update {
                it.copy(comments = it.comments.appendPage(page.items) { c -> c.id }, commentsNext = page.next)
            }
        }) { it.comments(postId, next) }
    }

    /** Публикация поменялась: заменить её везде, где она показана. */
    private fun replacePost(post: Post) {
        _state.update { s ->
            s.copy(
                feed = s.feed.map { if (it.id == post.id) post else it },
                personPosts = s.personPosts.map { if (it.id == post.id) post else it },
                openPost = if (s.openPost?.id == post.id) post else s.openPost,
            )
        }
    }

    private fun dropPost(id: Long) {
        _state.update { s ->
            s.copy(
                feed = s.feed.filterNot { it.id == id },
                personPosts = s.personPosts.filterNot { it.id == id },
                openPost = if (s.openPost?.id == id) null else s.openPost,
            )
        }
    }

    /**
     * Отметка «нравится» сразу на экране, запрос — следом. Ждать ответа
     * сервера ради сердечка значит дать человеку нажать дважды.
     */
    fun toggleLike(post: Post) {
        val liked = !post.liked
        replacePost(post.copy(liked = liked, likesCount = (post.likesCount + if (liked) 1 else -1).coerceAtLeast(0)))
        viewModelScope.launch {
            when (val outcome = api().likePost(post.id, liked)) {
                is Outcome.Ok -> replacePost(outcome.value)
                is Outcome.Fail -> {
                    replacePost(post)
                    handleFailure(outcome.message)
                }
            }
        }
    }

    fun createPost(text: String, photo: Uri?, visibility: String) {
        viewModelScope.launch {
            var jpeg: ByteArray? = null
            if (photo != null) {
                jpeg = readPhoto(photo, 1600)
                if (jpeg == null) {
                    say("Не удалось прочитать фото", isError = true)
                    return@launch
                }
            }
            run(onOk = { result: PostResult ->
                _state.update { s -> s.copy(feed = listOf(result.post) + s.feed.filterNot { it.id == result.post.id }) }
                back()
                say(result.notice.ifBlank { "Опубликовано" }, isError = false)
            }) { it.createPost(text, jpeg, visibility) }
        }
    }

    fun editPost(id: Long, text: String) = run(
        onOk = { post: Post ->
            replacePost(post)
            say("Изменения сохранены", isError = false)
        },
    ) { it.editPost(id, text) }

    fun deletePost(id: Long) = run(
        onOk = { text: String ->
            val onDetail = (_state.value.screen as? Screen.PostDetail)?.id == id
            dropPost(id)
            if (onDetail) back()
            say(text, isError = false)
        },
    ) { it.deletePost(id) }

    fun addComment(postId: Long, text: String) = run(
        onOk = { comment: PostComment ->
            _state.update { s -> s.copy(comments = listOf(comment) + s.comments.filterNot { it.id == comment.id }) }
            _state.value.openPost?.takeIf { it.id == postId }?.let {
                replacePost(it.copy(commentsCount = it.commentsCount + 1))
            }
        },
    ) { it.addComment(postId, text) }

    fun deleteComment(postId: Long, commentId: Long) = run(
        onOk = { text: String ->
            _state.update { s -> s.copy(comments = s.comments.filterNot { it.id == commentId }) }
            _state.value.openPost?.takeIf { it.id == postId }?.let {
                replacePost(it.copy(commentsCount = (it.commentsCount - 1).coerceAtLeast(0)))
            }
            say(text, isError = false)
        },
    ) { it.deleteComment(postId, commentId) }

    fun reportPost(postId: Long, commentId: Long?, reason: String, comment: String, alsoBlock: Boolean) = run(
        onOk = { text: String ->
            say(text, isError = false)
            if (alsoBlock) {
                if (commentId == null) dropPost(postId)
                reloadCurrent()
            }
        },
    ) { it.reportPost(postId, commentId, reason, comment, alsoBlock) }

    fun follow(userId: Int, follow: Boolean) = run(
        onOk = { info: FollowInfo ->
            _state.update { s ->
                val person = s.openPerson
                s.copy(openPerson = if (person?.id == userId) person.copy(follow = info) else person)
            }
            say(if (follow) "Вы подписались" else "Подписка отменена", isError = false)
        },
    ) { it.follow(userId, follow) }

    suspend fun postImageBytes(postId: Long): ByteArray? =
        (api().postImage(postId) as? Outcome.Ok)?.value

    // ==================== СООБЩЕСТВО: ЛЮДИ ====================

    /** Справочник интересов грузится один раз: он не меняется на ходу. */
    private fun ensureInterests() {
        if (_state.value.interests.isNotEmpty()) return
        run(onOk = { list -> _state.update { it.copy(interests = list) } }) { it.interests() }
    }

    fun searchPeople(filter: PeopleFilter) {
        _state.update { it.copy(peopleFilter = filter) }
        run(onOk = { page: Page<Person> ->
            _state.update { it.copy(people = page.items, peopleNext = page.next) }
        }) { it.people(filter) }
    }

    fun morePeople() {
        val s = _state.value
        val next = s.peopleNext ?: return
        run(onOk = { page: Page<Person> ->
            _state.update { it.copy(people = it.people.appendPage(page.items) { p -> p.id }, peopleNext = page.next) }
        }) { it.people(s.peopleFilter, pageNumber(next)) }
    }

    fun loadPerson(id: Int) {
        // Прежняя анкета убирается сразу: иначе, открыв второго человека,
        // секунду видишь первого — и можно успеть нажать «Написать» не тому
        if (_state.value.openPerson?.id != id) {
            _state.update { it.copy(openPerson = null, personPosts = emptyList(), personPostsNext = null) }
        }
        run(onOk = { person -> _state.update { it.copy(openPerson = person) } }) { it.person(id) }
        run(onOk = { page: Page<Post> ->
            _state.update { it.copy(personPosts = page.items, personPostsNext = page.next) }
        }) { it.personPosts(id) }
    }

    fun loadSocialProfile() = run(
        onOk = { profile -> _state.update { it.copy(socialProfile = profile) } },
    ) { it.socialProfile() }

    fun saveSocialProfile(profile: SocialProfile) = run(
        onOk = { saved ->
            _state.update { it.copy(socialProfile = saved) }
            say(
                if (saved.isDiscoverable) "Анкета сохранена и видна в поиске" else "Анкета сохранена",
                isError = false,
            )
        },
    ) { it.saveSocialProfile(profile) }

    /** Перевод человеку из его анкеты: имя получателя подставляется в форму. */
    fun transferTo(username: String) {
        _state.update { it.copy(transferPrefill = username) }
        go(Screen.Transfer)
    }

    fun consumeTransferPrefill() {
        _state.update { it.copy(transferPrefill = "") }
    }

    // ==================== СООБЩЕСТВО: ЧАТЫ ====================

    fun loadChats() = run(
        onOk = { page: Page<ChatInfo> -> _state.update { it.copy(chats = page.items, chatsNext = page.next) } },
    ) { it.chats() }

    fun moreChats() {
        val next = _state.value.chatsNext ?: return
        run(onOk = { page: Page<ChatInfo> ->
            _state.update { it.copy(chats = it.chats.appendPage(page.items) { c -> c.id }, chatsNext = page.next) }
        }) { it.chats(pageNumber(next)) }
    }

    /** Личный чат с человеком: существующий или новый. */
    fun writeTo(username: String) = run(
        onOk = { chat -> go(Screen.Chat(chat.id)) },
    ) { it.openChat(listOf(username), "") }

    /**
     * Новый чат с экрана «Новый чат». Сам этот экран из стека убирается:
     * «назад» из созданного чата должно вести к списку чатов, а не
     * обратно в форму, которую уже отправили.
     */
    fun createChat(usernames: List<String>, title: String) = run(
        onOk = { chat -> replaceTop(Screen.Chat(chat.id)) },
    ) { it.openChat(usernames, title) }

    private fun replaceTop(screen: Screen) {
        _state.update { it.copy(stack = it.stack.dropLast(1) + screen, message = null) }
        loadFor(screen)
    }

    private fun openChatThread(id: Int) {
        if (_state.value.openChat?.id != id) {
            _state.update { it.copy(openChat = null, chatMessages = emptyList(), chatHasOlder = false) }
        }
        chatSince = null
        run(onOk = { chat -> _state.update { it.copy(openChat = chat) } }) { it.chat(id) }
        run(onOk = { update: MessagesUpdate ->
            chatSince = id to update.serverTime
            mergeMessages(id, update.items, replace = true)
            _state.update { it.copy(chatHasOlder = update.hasMore) }
        }) { it.messages(id, null) }
    }

    /**
     * Метка времени сервера для опроса правок: с ней сервер вернёт
     * исправленные и удалённые после неё сообщения. Привязана к чату,
     * чтобы ответ закрытого чата не сбил метку открытого.
     */
    private var chatSince: Pair<Int, String>? = null

    fun loadOlderMessages(chatId: Int) {
        val first = _state.value.chatMessages.firstOrNull() ?: return
        run(onOk = { page: Page<ChatMessage> ->
            mergeMessages(chatId, page.items)
            _state.update { it.copy(chatHasOlder = page.next != null) }
        }) { it.olderMessages(chatId, first.id) }
    }

    fun editMessage(chatId: Int, messageId: Long, text: String) = run(
        onOk = { message: ChatMessage -> mergeMessages(chatId, listOf(message)) },
    ) { it.editMessage(chatId, messageId, text) }

    fun deleteMessage(chatId: Int, messageId: Long) = run(
        onOk = { message: ChatMessage -> mergeMessages(chatId, listOf(message)) },
    ) { it.deleteMessage(chatId, messageId) }

    /**
     * Сообщения добавляются с отбором по номеру: опрос и отправка могут
     * принести одно и то же сообщение, и без отбора оно показывалось бы
     * дважды. Ответ для чата, который уже закрыт, отбрасывается.
     */
    private fun mergeMessages(chatId: Int, incoming: List<ChatMessage>, replace: Boolean = false) {
        _state.update { current ->
            if ((current.screen as? Screen.Chat)?.id != chatId) return@update current
            val base = if (replace) emptyList() else current.chatMessages
            val merged = (base + incoming).associateBy { it.id }.values.sortedBy { it.id }
            current.copy(chatMessages = merged)
        }
    }

    fun sendMessage(chatId: Int, text: String) = run(
        onOk = { message -> mergeMessages(chatId, listOf(message)) },
    ) { it.sendMessage(chatId, text) }

    /**
     * Опрос открытого чата.
     *
     * Без полосы ожидания и без сообщений об ошибке: он идёт каждые
     * несколько секунд, и мигающая полоса или всплывающее «сервер не
     * отвечает» раз в четыре секунды сделали бы экран непригодным.
     * Исключение — просроченный вход: его нужно обработать как обычно.
     */
    fun pollChat(chatId: Int) {
        val after = _state.value.chatMessages.lastOrNull()?.id
        viewModelScope.launch {
            val since = chatSince?.takeIf { it.first == chatId }?.second
            val outcome = api().messages(chatId, after, since)
            if (outcome is Outcome.Ok) {
                val update = outcome.value
                if ((_state.value.screen as? Screen.Chat)?.id == chatId) {
                    if (update.serverTime.isNotBlank()) chatSince = chatId to update.serverTime
                    if (update.items.isNotEmpty() || update.changed.isNotEmpty()) {
                        mergeMessages(chatId, update.items + update.changed)
                    }
                }
            } else if (outcome is Outcome.Fail && outcome.message == SESSION_EXPIRED) {
                handleFailure(outcome.message)
            }
        }
    }

    fun leaveChat(chatId: Int) = run(
        onOk = { text ->
            say(text, isError = false)
            goRoot(Screen.Chats)
        },
    ) { it.leaveChat(chatId) }

    // ==================== ФОТО ====================

    /** Снимок из галереи → JPEG нужного размера; работа с файлом — не в главном потоке. */
    private suspend fun readPhoto(uri: Uri, maxSide: Int): ByteArray? =
        withContext(Dispatchers.IO) { ImageTools.prepareJpeg(getApplication<Application>(), uri, maxSide) }

    fun uploadAvatar(uri: Uri) {
        viewModelScope.launch {
            val jpeg = readPhoto(uri, 1024)
            if (jpeg == null) {
                say("Не удалось прочитать фото", isError = true)
                return@launch
            }
            run(onOk = { profile ->
                _state.update { it.copy(socialProfile = profile) }
                say("Фото обновлено", isError = false)
            }) { it.uploadAvatar(jpeg) }
        }
    }

    fun removeAvatar() = run(
        onOk = { profile -> _state.update { it.copy(socialProfile = profile) } },
    ) { it.removeAvatar() }

    fun sendImage(chatId: Int, uri: Uri, caption: String) {
        viewModelScope.launch {
            val jpeg = readPhoto(uri, 1600)
            if (jpeg == null) {
                say("Не удалось прочитать фото", isError = true)
                return@launch
            }
            run(onOk = { message -> mergeMessages(chatId, listOf(message)) }) {
                it.sendImage(chatId, caption, jpeg)
            }
        }
    }

    /** Байты фото для RemoteImage; null при любой неудаче — экран покажет подложку. */
    suspend fun avatarBytes(userId: Int): ByteArray? =
        (api().avatar(userId) as? Outcome.Ok)?.value

    suspend fun messageImageBytes(chatId: Int, messageId: Long): ByteArray? =
        (api().messageImage(chatId, messageId) as? Outcome.Ok)?.value

    // ==================== ЧЁРНЫЙ СПИСОК И ЖАЛОБЫ ====================

    fun loadBlocks() = run(
        onOk = { list -> _state.update { it.copy(blocks = list) } },
    ) { it.blocks() }

    /**
     * После блокировки и разблокировки текущий экран перечитывается:
     * в чате меняется плашка и доступность поля ввода, в анкете — кнопки,
     * в общем чате сообщения заблокированного скрываются.
     */
    private fun reloadCurrent() = loadFor(_state.value.screen)

    fun block(username: String) = run(
        onOk = { text ->
            say(text, isError = false)
            reloadCurrent()
        },
    ) { it.block(username) }

    fun unblock(userId: Int) = run(
        onOk = { text ->
            say(text, isError = false)
            reloadCurrent()
        },
    ) { it.unblock(userId) }

    fun reportMessage(chatId: Int, messageId: Long, reason: String, comment: String, alsoBlock: Boolean) = run(
        onOk = { text ->
            say(text, isError = false)
            if (alsoBlock) reloadCurrent()
        },
    ) { it.reportMessage(chatId, messageId, reason, comment, alsoBlock) }

    // ==================== СООБЩЕСТВО: ГРУППЫ ====================

    fun loadGroups(search: String, mineOnly: Boolean) {
        _state.update { it.copy(groupsMineOnly = mineOnly, groupsSearch = search) }
        run(onOk = { page: Page<Community> ->
            _state.update { it.copy(groups = page.items, groupsNext = page.next) }
        }) { it.communities(search, mineOnly) }
    }

    fun moreGroups() {
        val s = _state.value
        val next = s.groupsNext ?: return
        run(onOk = { page: Page<Community> ->
            _state.update { it.copy(groups = it.groups.appendPage(page.items) { g -> g.id }, groupsNext = page.next) }
        }) { it.communities(s.groupsSearch, s.groupsMineOnly, pageNumber(next)) }
    }

    fun loadGroup(id: Int) {
        if (_state.value.openGroup?.id != id) _state.update { it.copy(openGroup = null) }
        run(onOk = { group -> _state.update { it.copy(openGroup = group) } }) { it.community(id) }
    }

    fun createGroup(name: String, description: String, topic: String, isPrivate: Boolean) = run(
        onOk = { group ->
            say("Группа «${group.name}» создана", isError = false)
            replaceTop(Screen.GroupDetail(group.id))
        },
    ) { it.createCommunity(name, description, topic, isPrivate) }

    /**
     * Вступить, выйти, принять или отклонить заявку; для администраторов —
     * назначить роль (role), передать владение (transfer), исключить (remove).
     */
    fun groupAction(id: Int, action: String, userId: Int? = null, role: String? = null) = run(
        onOk = { reply ->
            _state.update { it.copy(openGroup = reply.community) }
            say(reply.message, isError = false)
        },
    ) { it.communityAction(id, action, userId, role) }

    fun updateGroup(id: Int, name: String, description: String, topic: String, isPrivate: Boolean) = run(
        onOk = { group: Community ->
            _state.update { it.copy(openGroup = group) }
            say("Группа сохранена", isError = false)
            back()
        },
    ) { it.updateCommunity(id, name, description, topic, isPrivate) }

    fun deleteGroup(id: Int) = run(
        onOk = { text: String ->
            _state.update { s -> s.copy(openGroup = null, groups = s.groups.filterNot { it.id == id }) }
            say(text, isError = false)
            goRoot(Screen.Groups)
        },
    ) { it.deleteCommunity(id) }

    // ==================== НАСТРОЙКИ ====================

    fun setBaseUrl(value: String) {
        prefs.baseUrl = value
        _state.value = _state.value.copy(baseUrl = prefs.baseUrl)
    }

    /**
     * Переключение демо-режима выбрасывает из учётной записи намеренно:
     * токен от настоящего сервера в демо-режиме бессмыслен, а выдуманный
     * токен демо-режима на настоящем сервере даст 401 на каждом экране —
     * и это выглядело бы как поломка приложения.
     */
    fun setDemoMode(enabled: Boolean) {
        prefs.demoMode = enabled
        prefs.clearTokens()
        _state.value = UiState(
            stack = listOf(Screen.Login),
            demoMode = enabled,
            baseUrl = prefs.baseUrl,
        )
        say(
            if (enabled) {
                "Демо-режим включён: данные выдуманы, сеть не используется. " +
                    "Войти можно с любым именем и паролем."
            } else {
                "Демо-режим выключен: приложение снова ходит на ${prefs.baseUrl}"
            },
            isError = false,
        )
    }

    /** Экран входа сам решает, показывать ли поле кода. */
    fun setTwoFactorRequired(value: Boolean) {
        _state.value = _state.value.copy(twoFactorRequired = value)
    }

    val currentUsername: String get() = prefs.username
}
