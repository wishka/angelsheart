package ru.angelhelper.app.data

/**
 * Сообщество в демо-режиме: выдуманные люди, чаты и группы в памяти.
 *
 * Правила повторяют сервер (social/services.py): личный чат с человеком
 * один, владелец не выходит из группы, закрытая группа — по заявке,
 * в поиске только открывшие анкету. Иначе демо-режим показывал бы
 * поведение, которого на самом деле нет.
 *
 * Состояние живёт до перезапуска приложения, как и остальной демо-режим.
 */
object DemoSocial {

    const val ME = "demo_user"
    private const val MY_ID = 1

    val interests = listOf(
        Interest("volunteering", "Волонтёрство"),
        Interest("animals", "Помощь животным"),
        Interest("health", "Здоровье"),
        Interest("education", "Образование"),
        Interest("ecology", "Экология"),
        Interest("sport", "Спорт"),
        Interest("travel", "Путешествия"),
        Interest("music", "Музыка"),
        Interest("books", "Книги"),
        Interest("photo", "Фотография"),
        Interest("family", "Семья и дети"),
    )

    private fun tags(vararg slugs: String) = interests.filter { it.slug in slugs }

    private val people = listOf(
        Person(2, "anna_petrova", "Анна Петрова", "Москва", 27, "female",
            "Волонтёр фонда, по выходным — в хосписе.", tags("volunteering", "health", "books")),
        Person(3, "sergey_ivanov", "Сергей Иванов", "Санкт-Петербург", 41, "male",
            "Помогаю приютам с ремонтом и транспортом.", tags("animals", "volunteering")),
        Person(4, "maria_k", "Мария К.", "Казань", 33, "female",
            "Учитель, собираю книги для сельских школ.", tags("education", "books", "family")),
        Person(5, "pavel_r", "Павел", "Новосибирск", 24, "male",
            "Бегаю марафоны в поддержку сборов.", tags("sport", "travel")),
        Person(6, "olga_eco", "Ольга", "Москва", 36, "female",
            "Раздельный сбор и субботники.", tags("ecology", "volunteering", "family")),
        Person(7, "dmitry_photo", "Дмитрий", "Екатеринбург", 29, "male",
            "Снимаю для благотворительных проектов бесплатно.", tags("photo", "music")),
        Person(8, "elena_vet", "Елена", "Санкт-Петербург", 45, "female",
            "Ветеринар, лечу животных из приютов.", tags("animals", "health")),
    )

    private var profile = SocialProfile(
        username = ME, displayName = "", city = "", birthYear = null, gender = "",
        about = "", interests = emptyList(), isDiscoverable = false, distributionConsent = false,
    )

    private class DemoChat(
        val id: Int,
        val kind: String,
        val title: String,
        val members: MutableList<ChatMember>,
        val messages: MutableList<ChatMessage> = mutableListOf(),
        var readUpTo: Long = 0,
        val communityId: Int? = null,
    )

    private class DemoCommunity(
        val id: Int,
        val name: String,
        val description: String,
        val topic: Interest?,
        val isPrivate: Boolean,
        val owner: String,
        var myStatus: String,
        var membersCount: Int,
        var chatId: Int,
        val pending: MutableList<ChatMember> = mutableListOf(),
    )

    private val me = ChatMember(MY_ID, ME, "Вы")
    private fun memberOf(person: Person) = ChatMember(person.id, person.username, person.displayName)

    private var nextMessageId = 100L
    private var nextChatId = 10
    private var nextCommunityId = 10

    private fun message(sender: String, text: String, at: String): ChatMessage =
        ChatMessage(++nextMessageId, sender, text, at, isMine = sender == "Вы")

    private val chats = mutableListOf(
        DemoChat(1, "direct", "", mutableListOf(me, memberOf(people[0]))).apply {
            messages += message("Анна Петрова", "Привет! Видела, ты помог со сбором для Ани. Спасибо 🙏", "2026-09-14T10:20:00Z")
            messages += message("Вы", "Привет! Рад был помочь. Как она?", "2026-09-14T10:25:00Z")
            messages += message("Анна Петрова", "Уже начала занятия с врачом. Пришлю фото на выходных!", "2026-09-14T10:31:00Z")
            readUpTo = messages[1].id
        },
        DemoChat(2, "community", "Волонтёры Москвы", mutableListOf(me, memberOf(people[0]), memberOf(people[5])), communityId = 1).apply {
            messages += message("Ольга", "В субботу субботник в Сокольниках, кто с нами?", "2026-09-13T08:00:00Z")
            messages += message("Анна Петрова", "Я буду!", "2026-09-13T08:12:00Z")
            readUpTo = messages.last().id
        },
    )

    private val communities = mutableListOf(
        DemoCommunity(1, "Волонтёры Москвы", "Субботники, помощь хосписам и приютам. Встречаемся каждые выходные.",
            interests.first { it.slug == "volunteering" }, false, "olga_eco", "member", 34, chatId = 2),
        DemoCommunity(2, "Хвосты и лапы", "Помогаем приютам: передержка, корм, лечение.",
            interests.first { it.slug == "animals" }, false, "elena_vet", "none", 112, chatId = 0),
        DemoCommunity(3, "Книги в сёла", "Собираем и отправляем книги в сельские библиотеки и школы.",
            interests.first { it.slug == "education" }, true, "maria_k", "none", 18, chatId = 0),
        DemoCommunity(4, "Бег во благо", "Благотворительные забеги: каждый километр — пожертвование.",
            interests.first { it.slug == "sport" }, false, "pavel_r", "none", 57, chatId = 0),
    )

    // ==================== АНКЕТА И ПОИСК ====================

    fun profile(): Outcome<SocialProfile> = Outcome.Ok(profile)

    fun saveProfile(update: SocialProfile): Outcome<SocialProfile> {
        val year = 2026
        val birthYear = update.birthYear
        if (birthYear != null && (birthYear > year - 14 || birthYear < year - 110)) {
            return Outcome.Fail("Проверьте год рождения")
        }
        profile = update.copy(
            username = ME,
            // Как на сервере: включение показа в поиске фиксирует согласие
            distributionConsent = profile.distributionConsent || update.isDiscoverable,
        )
        return Outcome.Ok(profile)
    }

    fun people(filter: PeopleFilter): Outcome<List<Person>> {
        val query = filter.query.trim().removePrefix("@").lowercase()
        val city = filter.city.trim().lowercase()
        return Outcome.Ok(
            people.filter { person ->
                (query.isEmpty() || "${person.username} ${person.displayName} ${person.city}".lowercase().contains(query)) &&
                    (city.isEmpty() || person.city.lowercase() == city) &&
                    (filter.gender.isBlank() || person.gender == filter.gender) &&
                    (filter.ageMin == null || (person.age ?: 0) >= filter.ageMin) &&
                    (filter.ageMax == null || (person.age ?: Int.MAX_VALUE) <= filter.ageMax) &&
                    (filter.interests.isEmpty() || person.interests.any { it.slug in filter.interests })
            }.sortedBy { it.username }
        )
    }

    fun person(id: Int): Outcome<Person> =
        people.firstOrNull { it.id == id }?.let { Outcome.Ok(it) }
            ?: Outcome.Fail("Пользователь не найден или скрыл анкету")

    // ==================== ЧАТЫ ====================

    private fun info(chat: DemoChat, withMembers: Boolean): ChatInfo {
        val peer = if (chat.kind == "direct") chat.members.firstOrNull { it.username != ME } else null
        return ChatInfo(
            id = chat.id,
            kind = chat.kind,
            title = peer?.displayName ?: chat.title,
            membersCount = chat.members.size,
            peerUsername = peer?.username,
            communityId = chat.communityId,
            lastMessage = chat.messages.lastOrNull(),
            unreadCount = chat.messages.count { it.id > chat.readUpTo && !it.isMine },
            lastActivityAt = chat.messages.lastOrNull()?.createdAt ?: "",
            members = if (withMembers) chat.members.toList() else emptyList(),
        )
    }

    fun chats(): Outcome<List<ChatInfo>> = Outcome.Ok(
        chats.filter { chat -> chat.members.any { it.username == ME } }
            .sortedByDescending { it.messages.lastOrNull()?.id ?: 0L }
            .map { info(it, withMembers = false) }
    )

    fun chat(id: Int): Outcome<ChatInfo> =
        chats.firstOrNull { it.id == id }?.let { Outcome.Ok(info(it, withMembers = true)) }
            ?: Outcome.Fail("Чат не найден")

    fun openChat(usernames: List<String>, title: String): Outcome<ChatInfo> {
        val names = usernames.map { it.trim().removePrefix("@") }.filter { it.isNotEmpty() }.distinct()
        if (names.isEmpty()) return Outcome.Fail("Укажите, кому написать")
        if (ME in names) return Outcome.Fail("Нельзя написать самому себе")
        val found = names.mapNotNull { name -> people.firstOrNull { it.username == name } }
        val missing = names - found.map { it.username }.toSet()
        if (missing.isNotEmpty()) return Outcome.Fail("Не найдены: " + missing.sorted().joinToString())

        if (found.size == 1 && title.isBlank()) {
            val existing = chats.firstOrNull { chat ->
                chat.kind == "direct" && chat.members.any { it.username == found[0].username }
            }
            if (existing != null) return Outcome.Ok(info(existing, withMembers = true))
            val chat = DemoChat(++nextChatId, "direct", "", mutableListOf(me, memberOf(found[0])))
            chats += chat
            return Outcome.Ok(info(chat, withMembers = true))
        }
        if (title.isBlank()) return Outcome.Fail("Укажите название чата")
        val chat = DemoChat(++nextChatId, "group", title.trim(), (listOf(me) + found.map(::memberOf)).toMutableList())
        chats += chat
        return Outcome.Ok(info(chat, withMembers = true))
    }

    fun messages(chatId: Int, afterId: Long?): Outcome<List<ChatMessage>> {
        val chat = chats.firstOrNull { it.id == chatId } ?: return Outcome.Fail("Чат не найден")
        val list = if (afterId == null) chat.messages.takeLast(50) else chat.messages.filter { it.id > afterId }
        list.lastOrNull()?.let { chat.readUpTo = maxOf(chat.readUpTo, it.id) }
        return Outcome.Ok(list.toList())
    }

    fun send(chatId: Int, text: String): Outcome<ChatMessage> {
        val chat = chats.firstOrNull { it.id == chatId } ?: return Outcome.Fail("Чат не найден")
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return Outcome.Fail("Сообщение пустое")
        if (trimmed.length > 2000) return Outcome.Fail("Сообщение длиннее 2000 символов")
        val sent = message("Вы", trimmed, "только что")
        chat.messages += sent
        chat.readUpTo = sent.id
        return Outcome.Ok(sent)
    }

    fun leaveChat(chatId: Int): Outcome<String> {
        val chat = chats.firstOrNull { it.id == chatId } ?: return Outcome.Fail("Чат не найден")
        return when (chat.kind) {
            "direct" -> Outcome.Fail("Из личного чата выйти нельзя")
            "community" -> Outcome.Fail("Это обсуждение группы — чтобы уйти, выйдите из группы")
            else -> {
                chats.remove(chat)
                Outcome.Ok("Вы вышли из чата")
            }
        }
    }

    // ==================== ГРУППЫ ====================

    private fun view(group: DemoCommunity): Community {
        val member = group.myStatus in setOf("member", "admin", "owner")
        val chat = chats.firstOrNull { it.id == group.chatId }
        return Community(
            id = group.id,
            name = group.name,
            description = group.description,
            topic = group.topic,
            isPrivate = group.isPrivate,
            membersCount = group.membersCount,
            ownerUsername = group.owner,
            myStatus = group.myStatus,
            chatId = if (member && chat != null) chat.id else null,
            members = if (member) {
                (chat?.members ?: listOf(me)).map {
                    CommunityMember(it.id, it.username, it.displayName,
                        if (it.username == group.owner) "owner" else "member")
                }
            } else emptyList(),
            pendingRequests = if (group.myStatus == "owner" || group.myStatus == "admin") group.pending.toList() else emptyList(),
        )
    }

    fun communities(search: String, mineOnly: Boolean): Outcome<List<Community>> {
        val needle = search.trim().lowercase()
        return Outcome.Ok(
            communities
                .filter { !mineOnly || it.myStatus != "none" }
                .filter { needle.isEmpty() || "${it.name} ${it.description}".lowercase().contains(needle) }
                .map(::view)
        )
    }

    fun community(id: Int): Outcome<Community> =
        communities.firstOrNull { it.id == id }?.let { Outcome.Ok(view(it)) }
            ?: Outcome.Fail("Группа не найдена")

    fun create(name: String, description: String, topic: String, isPrivate: Boolean): Outcome<Community> {
        if (name.isBlank()) return Outcome.Fail("Укажите название группы")
        val groupId = ++nextCommunityId
        val chat = DemoChat(++nextChatId, "community", name.trim(), mutableListOf(me), communityId = groupId)
        chats += chat
        val group = DemoCommunity(
            id = groupId,
            name = name.trim(),
            description = description.trim(),
            topic = interests.firstOrNull { it.slug == topic },
            isPrivate = isPrivate,
            owner = ME,
            myStatus = "owner",
            membersCount = 1,
            chatId = chat.id,
        )
        // Чужая заявка в новую закрытую группу — чтобы в демо было что одобрить
        if (isPrivate) group.pending += memberOf(people[1])
        communities.add(0, group)
        return Outcome.Ok(view(group))
    }

    fun action(id: Int, action: String, userId: Int?): Outcome<CommunityReply> {
        val group = communities.firstOrNull { it.id == id } ?: return Outcome.Fail("Группа не найдена")
        val text = when (action) {
            "join" -> when {
                group.myStatus == "pending" -> return Outcome.Fail("Заявка уже отправлена")
                group.myStatus != "none" -> return Outcome.Fail("Вы уже в группе")
                group.isPrivate -> {
                    group.myStatus = "pending"
                    "Заявка отправлена. Её рассмотрит администратор группы."
                }
                else -> {
                    group.myStatus = "member"
                    group.membersCount += 1
                    joinChat(group)
                    "Вы вступили в группу «${group.name}»"
                }
            }
            "leave" -> when (group.myStatus) {
                "none" -> return Outcome.Fail("Вы не состоите в группе")
                "owner" -> return Outcome.Fail("Владелец не может выйти из своей группы")
                "pending" -> {
                    group.myStatus = "none"
                    "Заявка отозвана"
                }
                else -> {
                    group.myStatus = "none"
                    group.membersCount -= 1
                    chats.firstOrNull { it.id == group.chatId }?.members?.removeAll { it.username == ME }
                    "Вы вышли из группы"
                }
            }
            "approve", "decline" -> {
                if (group.myStatus != "owner" && group.myStatus != "admin") {
                    return Outcome.Fail("Это может только администратор группы")
                }
                val request = group.pending.firstOrNull { it.id == userId }
                    ?: return Outcome.Fail("Заявка не найдена")
                group.pending.remove(request)
                if (action == "approve") {
                    group.membersCount += 1
                    chats.firstOrNull { it.id == group.chatId }?.members?.add(request)
                    "${request.username} принят в группу"
                } else {
                    "Заявка отклонена"
                }
            }
            else -> return Outcome.Fail("Неизвестное действие")
        }
        return Outcome.Ok(CommunityReply(view(group), text))
    }

    /** Группе без обсуждения в демо-данных оно создаётся при вступлении. */
    private fun joinChat(group: DemoCommunity) {
        val existing = chats.firstOrNull { it.id == group.chatId }
        if (existing != null) {
            if (existing.members.none { it.username == ME }) existing.members += me
            return
        }
        val chat = DemoChat(++nextChatId, "community", group.name, mutableListOf(me), communityId = group.id)
        chats += chat
        group.chatId = chat.id
    }
}
