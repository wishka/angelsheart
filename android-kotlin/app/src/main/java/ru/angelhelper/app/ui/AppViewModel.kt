package ru.angelhelper.app.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.update
import ru.angelhelper.app.data.Api
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

    // Сообщество
    data object People : Screen
    data class PersonDetail(val id: Int) : Screen
    data object Groups : Screen
    data class GroupDetail(val id: Int) : Screen
    data object NewGroup : Screen
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
    val openFundraise: Fundraise? = null,
    val transactions: List<Tx> = emptyList(),
    val leaders: List<Leader> = emptyList(),
    val consents: List<Consent> = emptyList(),
    val demoMode: Boolean = false,
    val baseUrl: String = AppPrefs.DEFAULT_BASE_URL,
    /** Сервер попросил код двухфакторной проверки: экран входа раскрывает поле. */
    val twoFactorRequired: Boolean = false,

    // ---- Сообщество ----
    val interests: List<Interest> = emptyList(),
    val peopleFilter: PeopleFilter = PeopleFilter(),
    val people: List<Person> = emptyList(),
    val openPerson: Person? = null,
    val socialProfile: SocialProfile? = null,
    val chats: List<ChatInfo> = emptyList(),
    val openChat: ChatInfo? = null,
    val chatMessages: List<ChatMessage> = emptyList(),
    val groups: List<Community> = emptyList(),
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
            stack = listOf(if (prefs.signedIn) Screen.Dashboard else Screen.Login),
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
            Screen.Dashboard -> refreshDashboard()
            Screen.Fundraises -> loadFundraises("")
            is Screen.FundraiseDetail -> loadFundraise(screen.id)
            Screen.History -> loadTransactions()
            Screen.Profile -> loadProfile()
            Screen.Leaders -> loadLeaders()
            Screen.Consents -> loadConsents()
            Screen.People -> {
                ensureInterests()
                searchPeople(_state.value.peopleFilter)
            }
            is Screen.PersonDetail -> loadPerson(screen.id)
            Screen.Groups -> loadGroups("", _state.value.groupsMineOnly)
            is Screen.GroupDetail -> loadGroup(screen.id)
            Screen.NewGroup -> ensureInterests()
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
            if (prefs.username.isBlank()) prefs.username = username
            goRoot(Screen.Dashboard)
        },
    ) { it.login(username, password, code) }

    fun register(username: String, email: String, password: String, consents: Boolean) = run(
        onOk = { tokens ->
            // Сервер выдал токены при регистрации — входим сразу, не
            // отправляя человека набирать тот же пароль второй раз
            prefs.saveTokens(tokens)
            if (prefs.username.isBlank()) prefs.username = username
            goRoot(Screen.Dashboard)
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

    fun loadFundraises(search: String) = run(
        onOk = { list -> _state.value = _state.value.copy(fundraises = list) },
    ) { it.fundraises(search) }

    fun loadFundraise(id: Int) = run(
        onOk = { item -> _state.value = _state.value.copy(openFundraise = item) },
    ) { it.fundraise(id) }

    fun loadTransactions() = run(
        onOk = { list -> _state.value = _state.value.copy(transactions = list) },
    ) { it.transactions() }

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

    fun topUp(amount: String, method: String) = run(
        onOk = { text ->
            say(text, isError = false)
            refreshDashboard()
        },
    ) { it.topUp(amount, method) }

    fun withdraw(amount: String, method: String, target: String) = run(
        onOk = { text -> say(text, isError = false) },
    ) { it.withdraw(amount, method, target) }

    fun submitVerification(fullName: String, birthDate: String, series: String, number: String) =
        run(
            onOk = { text -> say(text, isError = false) },
        ) { it.submitVerification(fullName, birthDate, series, number) }


    // ==================== СООБЩЕСТВО: ЛЮДИ ====================

    /** Справочник интересов грузится один раз: он не меняется на ходу. */
    private fun ensureInterests() {
        if (_state.value.interests.isNotEmpty()) return
        run(onOk = { list -> _state.update { it.copy(interests = list) } }) { it.interests() }
    }

    fun searchPeople(filter: PeopleFilter) {
        _state.update { it.copy(peopleFilter = filter) }
        run(onOk = { list -> _state.update { it.copy(people = list) } }) { it.people(filter) }
    }

    fun loadPerson(id: Int) {
        // Прежняя анкета убирается сразу: иначе, открыв второго человека,
        // секунду видишь первого — и можно успеть нажать «Написать» не тому
        if (_state.value.openPerson?.id != id) _state.update { it.copy(openPerson = null) }
        run(onOk = { person -> _state.update { it.copy(openPerson = person) } }) { it.person(id) }
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
        onOk = { list -> _state.update { it.copy(chats = list) } },
    ) { it.chats() }

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
            _state.update { it.copy(openChat = null, chatMessages = emptyList()) }
        }
        run(onOk = { chat -> _state.update { it.copy(openChat = chat) } }) { it.chat(id) }
        run(onOk = { list -> mergeMessages(id, list, replace = true) }) { it.messages(id, null) }
    }

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
            val outcome = api().messages(chatId, after)
            if (outcome is Outcome.Ok && outcome.value.isNotEmpty()) {
                mergeMessages(chatId, outcome.value)
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
        _state.update { it.copy(groupsMineOnly = mineOnly) }
        run(onOk = { list -> _state.update { it.copy(groups = list) } }) {
            it.communities(search, mineOnly)
        }
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

    /** Вступить, выйти, принять или отклонить заявку. */
    fun groupAction(id: Int, action: String, userId: Int? = null) = run(
        onOk = { reply ->
            _state.update { it.copy(openGroup = reply.community) }
            say(reply.message, isError = false)
        },
    ) { it.communityAction(id, action, userId) }

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
