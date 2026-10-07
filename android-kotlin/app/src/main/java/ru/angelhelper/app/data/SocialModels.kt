package ru.angelhelper.app.data

/**
 * Данные сообщества: анкеты, поиск людей, чаты и группы.
 *
 * Отдельным файлом от денежных моделей: у них разные серверные
 * приложения (main и social) и разные правила.
 */

data class Interest(
    val slug: String,
    val title: String,
)

/** Чужая анкета в поиске. Почты и баланса здесь нет и быть не должно. */
data class Person(
    val id: Int,
    val username: String,
    val displayName: String,
    val city: String,
    val age: Int?,
    val gender: String,
    val about: String,
    val interests: List<Interest>,
    /** Этот человек в моём чёрном списке. */
    val isBlocked: Boolean = false,
    val hasAvatar: Boolean = false,
    /** Меняется при смене фото — ключ кэша. */
    val avatarVersion: String = "",
) {
    /** Короткая строка под именем: «28 лет · Москва». */
    val summary: String
        get() = listOfNotNull(age?.let { ageTitle(it) }, city.takeIf { it.isNotBlank() })
            .joinToString(" · ")
}

/** Своя анкета — то, что человек сам решил о себе показать. */
data class SocialProfile(
    val username: String,
    val displayName: String,
    val city: String,
    val birthYear: Int?,
    val gender: String,
    val about: String,
    val interests: List<Interest>,
    val isDiscoverable: Boolean,
    /**
     * Действует ли согласие на распространение ПДн. Флажок «показывать
     * в поиске» без него ничего не даёт: согласие могли отозвать на
     * сайте, и экран должен объяснить, почему анкеты в поиске нет.
     */
    val distributionConsent: Boolean,
    val userId: Int = 0,
    val hasAvatar: Boolean = false,
    val avatarVersion: String = "",
)

/** Параметры поиска людей. Пустой фильтр — все, кто открыт для поиска. */
data class PeopleFilter(
    val query: String = "",
    val city: String = "",
    val gender: String = "",
    val ageMin: Int? = null,
    val ageMax: Int? = null,
    val interests: Set<String> = emptySet(),
) {
    /** Сколько фильтров включено помимо строки поиска — для подписи кнопки. */
    val activeCount: Int
        get() = listOf(
            city.isNotBlank(),
            gender.isNotBlank(),
            ageMin != null || ageMax != null,
            interests.isNotEmpty(),
        ).count { it }
}

data class ChatMessage(
    val id: Long,
    val senderName: String,
    val text: String,
    val createdAt: String,
    val isMine: Boolean,
    /** null — автор удалил учётную запись. */
    val senderId: Int? = null,
    /**
     * Текст подменён сервером: скрыто модератором или автор в моём
     * чёрном списке. На такое сообщение жаловаться уже незачем.
     */
    val isHidden: Boolean = false,
    /** В сообщении фото; загружается отдельно по id сообщения. */
    val hasImage: Boolean = false,
) {
    val canReport: Boolean get() = !isMine && !isHidden && senderId != null

    /** Для строки в списке чатов: фото без подписи — «📷 Фото». */
    val previewText: String
        get() = when {
            hasImage && text.isBlank() -> "📷 Фото"
            hasImage -> "📷 $text"
            else -> text
        }
}

data class ChatMember(
    val id: Int,
    val username: String,
    val displayName: String,
)

/**
 * Чат в списке и в шапке переписки.
 *
 * kind: direct — личный, group — групповой, community — обсуждение
 * группы. Участники приходят только в карточке чата, в списке пусто.
 */
data class ChatInfo(
    val id: Int,
    val kind: String,
    val title: String,
    val membersCount: Int,
    val peerUsername: String?,
    val communityId: Int?,
    val lastMessage: ChatMessage?,
    val unreadCount: Int,
    val lastActivityAt: String,
    val members: List<ChatMember> = emptyList(),
    val peerId: Int? = null,
    /** Для личного чата: none, blocked_by_me, blocked_me. */
    val blockStatus: String = "none",
    val peerHasAvatar: Boolean = false,
    val peerAvatarVersion: String = "",
) {
    val isDirect: Boolean get() = kind == "direct"
    val isBlocked: Boolean get() = blockStatus != "none"

    val kindTitle: String
        get() = when (kind) {
            "direct" -> "Личный чат"
            "community" -> "Обсуждение группы"
            else -> "Групповой чат · ${membersTitle(membersCount)}"
        }
}

data class CommunityMember(
    val id: Int,
    val username: String,
    val displayName: String,
    val role: String,
) {
    val roleTitle: String
        get() = when (role) {
            "owner" -> "владелец"
            "admin" -> "администратор"
            else -> ""
        }
}

/**
 * Группа по интересам.
 *
 * myStatus — отношение текущего пользователя к группе: none, pending
 * (заявка в закрытую группу), member, admin, owner. Участники и заявки
 * приходят только в карточке и только тем, кому их положено видеть.
 */
data class Community(
    val id: Int,
    val name: String,
    val description: String,
    val topic: Interest?,
    val isPrivate: Boolean,
    val membersCount: Int,
    val ownerUsername: String,
    val myStatus: String,
    val chatId: Int?,
    val members: List<CommunityMember> = emptyList(),
    val pendingRequests: List<ChatMember> = emptyList(),
) {
    val isMember: Boolean get() = myStatus == "member" || myStatus == "admin" || myStatus == "owner"
    val canManage: Boolean get() = myStatus == "admin" || myStatus == "owner"

    val summary: String
        get() = listOfNotNull(
            topic?.title,
            if (isPrivate) "закрытая" else "открытая",
            membersTitle(membersCount),
        ).joinToString(" · ")
}

/** Ответ на действие с группой: обновлённая карточка и слова для человека. */
data class CommunityReply(
    val community: Community,
    val message: String,
)

val GENDERS: List<Pair<String, String>> = listOf(
    "" to "Не важно",
    "female" to "Женский",
    "male" to "Мужской",
)

/** Русское склонение числительных: plural(5, "год", "года", "лет") → «5 лет». */
fun plural(n: Int, one: String, few: String, many: String): String {
    val mod10 = n % 10
    val mod100 = n % 100
    val word = when {
        mod10 == 1 && mod100 != 11 -> one
        mod10 in 2..4 && mod100 !in 12..14 -> few
        else -> many
    }
    return "$n $word"
}

fun ageTitle(age: Int): String = plural(age, "год", "года", "лет")

fun membersTitle(count: Int): String = plural(count, "участник", "участника", "участников")

/** Запись чёрного списка. */
data class BlockedUser(
    val id: Int,
    val username: String,
    val displayName: String,
    val createdAt: String,
)

/** Причины жалобы — в точности как REASON_CHOICES модели MessageReport. */
val REPORT_REASONS: List<Pair<String, String>> = listOf(
    "spam" to "Спам или реклама",
    "abuse" to "Оскорбления или травля",
    "fraud" to "Мошенничество",
    "illegal" to "Запрещённый контент",
    "other" to "Другое",
)
