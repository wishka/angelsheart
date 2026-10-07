package ru.angelhelper.app.data

/**
 * Лента, страницы списков и кошелёк.
 */

/**
 * Страница списка.
 *
 * next — чем просить следующую страницу: номер страницы («2») для
 * обычных списков или курсор «до id» для ленты, комментариев и
 * переписки. null — дальше ничего нет. Строкой, а не числом: так один
 * тип покрывает оба способа, и экрану не нужно знать, какой из них у
 * этого списка.
 */
data class Page<T>(
    val items: List<T>,
    val next: String?,
)

/** Склейка страниц без повторов: сервер мог вернуть уже показанную запись. */
fun <T, K> List<T>.appendPage(more: List<T>, key: (T) -> K): List<T> {
    val seen = mapTo(HashSet()) { key(it) }
    return this + more.filter { seen.add(key(it)) }
}

data class Author(
    val id: Int,
    val username: String,
    val displayName: String,
    val hasAvatar: Boolean,
    val avatarVersion: String,
)

/**
 * Публикация. visibility: public — всем (если анкета автора открыта),
 * followers — подписчикам.
 */
data class Post(
    val id: Long,
    val author: Author,
    val text: String,
    val hasImage: Boolean,
    val visibility: String,
    val createdAt: String,
    val edited: Boolean,
    val isMine: Boolean,
    val isHidden: Boolean,
    val likesCount: Int,
    val commentsCount: Int,
    val liked: Boolean,
) {
    val visibilityTitle: String get() = if (visibility == "public") "Всем" else "Подписчикам"
}

data class PostComment(
    val id: Long,
    val author: Author,
    val text: String,
    val createdAt: String,
    val isMine: Boolean,
    val canDelete: Boolean,
)

/** Результат публикации: сервер может сузить видимость и объяснить почему. */
data class PostResult(
    val post: Post,
    val notice: String,
)

/** Подписки человека: для карточки и кнопки «Подписаться». */
data class FollowInfo(
    val followersCount: Int,
    val followingCount: Int,
    val postsCount: Int,
    val isFollowing: Boolean,
    val canFollow: Boolean,
)

/** Опрос открытого чата: новые сообщения и правки уже показанных. */
data class MessagesUpdate(
    val items: List<ChatMessage>,
    val changed: List<ChatMessage>,
    val serverTime: String,
    /** При первой загрузке: есть ли сообщения старше показанных. */
    val hasMore: Boolean = false,
)

// ==================== КОШЕЛЁК ====================

data class Choice(val id: String, val title: String)

data class WalletInfo(
    val balance: String,
    val topupMin: String,
    val topupMax: String,
    val topupMethods: List<Choice>,
    /** Тестовый режим сервера: деньги зачисляются сразу, без ЮKassa. */
    val topupSimulated: Boolean,
    val withdrawMin: String,
    val withdrawMax: String,
    val withdrawMethods: List<Choice>,
    val sbpBanks: List<Choice>,
    val verificationLevel: String,
)

/**
 * Ответ на пополнение. confirmationUrl — страница оплаты ЮKassa:
 * приложение открывает её в браузере, деньги приходят по вебхуку.
 */
data class TopUpResult(
    val completed: Boolean,
    val message: String,
    val confirmationUrl: String?,
)

data class Withdrawal(
    val id: Int,
    val amount: String,
    val methodTitle: String,
    val details: String,
    val status: String,
    val statusTitle: String,
    val createdAt: String,
    val canCancel: Boolean,
)

data class KycDocument(
    val id: Int,
    val typeTitle: String,
    val statusTitle: String,
    val uploadedAt: String,
)

data class VerificationInfo(
    val level: String,
    val levelTitle: String,
    val submitted: Boolean,
    val passportMasked: String,
    val limits: Map<String, String>,
    val documents: List<KycDocument>,
    val documentTypes: List<Choice>,
)
