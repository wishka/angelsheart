package ru.angelhelper.app.data

/**
 * Лента в демо-режиме: выдуманные публикации людей из DemoSocial,
 * подписки, лайки, комментарии. Правила видимости — как на сервере
 * (social/feed.py): «Подписки» — свои и тех, на кого подписан; «Все» —
 * публичные; чёрный список прячет автора с его постами.
 */
object DemoFeed {

    private const val MY_ID = 1

    private data class Stored(
        val id: Long,
        val authorId: Int,
        var text: String,
        val visibility: String,
        val createdAt: String,
        var edited: Boolean = false,
        var hasImage: Boolean = false,
    )

    private data class StoredComment(val id: Long, val postId: Long, val authorId: Int, val text: String, val createdAt: String)

    private var nextId = 1000L
    private val follows = mutableSetOf(2, 6)          // демо-пользователь подписан на Анну и Ольгу
    private val likes = mutableSetOf<Pair<Long, Int>>()
    private val images = mutableMapOf<Long, ByteArray>()
    private val reported = mutableSetOf<Pair<Long, Long?>>()

    private val posts = mutableListOf(
        Stored(1, 2, "Сегодня отвезли в хоспис книги и пледы — спасибо всем, кто помог собрать! 💛", "public", "2026-10-06T18:20:00Z"),
        Stored(2, 3, "Приюту «Тёплый нос» нужны волонтёры на выходные: выгул и уборка. Кто со мной?", "public", "2026-10-06T12:05:00Z"),
        Stored(3, 6, "Субботник в Сокольниках прошёл отлично: 40 мешков мусора и новые знакомства.", "followers", "2026-10-05T16:40:00Z"),
        Stored(4, 5, "Пробежал благотворительные 10 км — каждый километр превратился в 500 ₽ для приюта.", "public", "2026-10-04T09:30:00Z"),
        Stored(5, 4, "Ищем книги для сельской библиотеки: детская литература и учебники 5–9 классов.", "public", "2026-10-03T14:00:00Z"),
    )

    private val comments = mutableListOf(
        StoredComment(1, 1, 6, "Какие вы молодцы!", "2026-10-06T18:40:00Z"),
        StoredComment(2, 2, 7, "Я в субботу смогу, напиши адрес", "2026-10-06T13:10:00Z"),
    )

    private fun author(id: Int): Author {
        if (id == MY_ID) return Author(MY_ID, DemoSocial.ME, "Вы", false, "")
        val person = DemoSocial.personById(id)
        return Author(id, person?.username ?: "user$id", person?.displayName ?: "Пользователь", false, "")
    }

    private fun view(stored: Stored) = Post(
        id = stored.id,
        author = author(stored.authorId),
        text = stored.text,
        hasImage = stored.hasImage,
        visibility = stored.visibility,
        createdAt = stored.createdAt,
        edited = stored.edited,
        isMine = stored.authorId == MY_ID,
        isHidden = false,
        likesCount = likes.count { it.first == stored.id },
        commentsCount = comments.count { it.postId == stored.id && !DemoSocial.isBlocked(it.authorId) },
        liked = (stored.id to MY_ID) in likes,
    )

    private fun visible(stored: Stored): Boolean =
        !DemoSocial.isBlocked(stored.authorId) &&
            (stored.authorId == MY_ID || stored.visibility == "public" || stored.authorId in follows)

    fun feed(scope: String): Outcome<Page<Post>> = Outcome.Ok(
        Page(
            posts.filter(::visible)
                .filter {
                    if (scope == "all") it.visibility == "public" || it.authorId == MY_ID
                    else it.authorId == MY_ID || it.authorId in follows
                }
                .sortedByDescending { it.id }
                .map(::view),
            null,
        )
    )

    fun personPosts(userId: Int): Outcome<Page<Post>> = Outcome.Ok(
        Page(posts.filter { it.authorId == userId && visible(it) }.sortedByDescending { it.id }.map(::view), null)
    )

    fun post(id: Long): Outcome<Post> =
        posts.firstOrNull { it.id == id && visible(it) }?.let { Outcome.Ok(view(it)) }
            ?: Outcome.Fail("Публикация не найдена")

    fun create(text: String, jpeg: ByteArray?, visibility: String, profilePublic: Boolean): Outcome<PostResult> {
        val trimmed = text.trim()
        if (trimmed.isEmpty() && jpeg == null) return Outcome.Fail("Публикация пустая")
        // Как на сервере: «всем» — только с открытой анкетой
        val narrowed = visibility == "public" && !profilePublic
        val stored = Stored(++nextId, MY_ID, trimmed, if (narrowed) "followers" else visibility, "только что",
            hasImage = jpeg != null)
        if (jpeg != null) images[stored.id] = jpeg
        posts.add(0, stored)
        val notice = if (narrowed) {
            "Публикация видна только подписчикам: чтобы публиковать для всех, откройте анкету для поиска."
        } else ""
        return Outcome.Ok(PostResult(view(stored), notice))
    }

    fun edit(id: Long, text: String): Outcome<Post> {
        val stored = posts.firstOrNull { it.id == id && it.authorId == MY_ID }
            ?: return Outcome.Fail("Редактировать можно только свою публикацию")
        if (text.isBlank() && !stored.hasImage) return Outcome.Fail("Публикация пустая")
        stored.text = text.trim()
        stored.edited = true
        return Outcome.Ok(view(stored))
    }

    fun delete(id: Long): Outcome<String> {
        val removed = posts.removeAll { it.id == id && it.authorId == MY_ID }
        return if (removed) Outcome.Ok("Публикация удалена") else Outcome.Fail("Удалить можно только свою публикацию")
    }

    fun like(id: Long, liked: Boolean): Outcome<Post> {
        val stored = posts.firstOrNull { it.id == id && visible(it) } ?: return Outcome.Fail("Публикация не найдена")
        if (liked) likes += id to MY_ID else likes -= id to MY_ID
        return Outcome.Ok(view(stored))
    }

    fun comments(postId: Long): Outcome<Page<PostComment>> {
        val stored = posts.firstOrNull { it.id == postId && visible(it) } ?: return Outcome.Fail("Публикация не найдена")
        return Outcome.Ok(
            Page(
                comments.filter { it.postId == postId && !DemoSocial.isBlocked(it.authorId) }.asReversed().map {  // новые сверху, как на сервере
                    PostComment(it.id, author(it.authorId), it.text, it.createdAt,
                        isMine = it.authorId == MY_ID, canDelete = it.authorId == MY_ID || stored.authorId == MY_ID)
                },
                null,
            )
        )
    }

    fun addComment(postId: Long, text: String): Outcome<PostComment> {
        if (posts.none { it.id == postId && visible(it) }) return Outcome.Fail("Публикация не найдена")
        if (text.isBlank()) return Outcome.Fail("Комментарий пустой")
        val comment = StoredComment(++nextId, postId, MY_ID, text.trim(), "только что")
        comments += comment
        return Outcome.Ok(PostComment(comment.id, author(MY_ID), comment.text, comment.createdAt, true, true))
    }

    fun deleteComment(postId: Long, commentId: Long): Outcome<String> {
        val stored = posts.firstOrNull { it.id == postId } ?: return Outcome.Fail("Публикация не найдена")
        val removed = comments.removeAll {
            it.id == commentId && it.postId == postId && (it.authorId == MY_ID || stored.authorId == MY_ID)
        }
        return if (removed) Outcome.Ok("Комментарий удалён") else Outcome.Fail("Комментарий не найден")
    }

    fun report(postId: Long, commentId: Long?, reason: String, alsoBlock: Boolean): Outcome<String> {
        val stored = posts.firstOrNull { it.id == postId } ?: return Outcome.Fail("Публикация не найдена")
        val authorId = if (commentId == null) stored.authorId else comments.firstOrNull { it.id == commentId }?.authorId
            ?: return Outcome.Fail("Комментарий не найден")
        if (authorId == MY_ID) return Outcome.Fail("Нельзя пожаловаться на свою публикацию")
        if (REPORT_REASONS.none { it.first == reason }) return Outcome.Fail("Укажите причину жалобы")
        if (!reported.add(postId to commentId)) return Outcome.Fail("Вы уже пожаловались")
        var text = "Жалоба отправлена. Модератор рассмотрит её. (демо-режим)"
        if (alsoBlock) {
            DemoSocial.block(author(authorId).username)
            follows -= authorId
            text += " ${author(authorId).username} добавлен в чёрный список."
        }
        return Outcome.Ok(text)
    }

    fun followInfo(userId: Int) = FollowInfo(
        followersCount = (if (userId in follows) 1 else 0) + 12,
        followingCount = 7,
        postsCount = posts.count { it.authorId == userId },
        isFollowing = userId in follows,
        canFollow = userId != MY_ID && !DemoSocial.isBlocked(userId),
    )

    fun follow(userId: Int, follow: Boolean): Outcome<FollowInfo> {
        if (userId == MY_ID) return Outcome.Fail("Нельзя подписаться на себя")
        if (DemoSocial.isBlocked(userId)) return Outcome.Fail("Подписаться на этого пользователя нельзя")
        if (follow) follows += userId else follows -= userId
        return Outcome.Ok(followInfo(userId))
    }

    fun image(id: Long): Outcome<ByteArray> = images[id]?.let { Outcome.Ok(it) } ?: Outcome.Fail("Изображения нет")
}
