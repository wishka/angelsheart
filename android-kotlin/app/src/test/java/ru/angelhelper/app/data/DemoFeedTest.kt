package ru.angelhelper.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Демо-лента повторяет правила сервера (social/feed.py): по ним человек
 * знакомится с приложением без сервера, и расхождение выглядело бы как
 * ошибка приложения. Состояние общее на все тесты, поэтому каждый тест
 * работает со своими записями.
 */
class DemoFeedTest {

    @Test
    fun publicPostNarrowedWithoutOpenProfile() {
        val result = DemoFeed.create("Привет всем", null, "public", profilePublic = false).ok()
        assertEquals("followers", result.post.visibility)
        assertTrue(result.notice.isNotBlank())

        val open = DemoFeed.create("Привет всем", null, "public", profilePublic = true).ok()
        assertEquals("public", open.post.visibility)
        assertEquals("", open.notice)
    }

    @Test
    fun emptyPostRejected() {
        DemoFeed.create("   ", null, "followers", profilePublic = false).failure()
    }

    @Test
    fun ownPostOnTopOfFollowingFeed() {
        val post = DemoFeed.create("Моя запись", null, "followers", profilePublic = false).ok().post
        val feed = DemoFeed.feed("following").ok().items
        assertEquals(post.id, feed.first().id)
        assertTrue(feed.first().isMine)
    }

    @Test
    fun likeAndUnlike() {
        val post = DemoFeed.create("Лайкни меня", null, "followers", profilePublic = false).ok().post
        val liked = DemoFeed.like(post.id, true).ok()
        assertTrue(liked.liked)
        assertEquals(1, liked.likesCount)
        // повторная отметка не удваивает счётчик
        assertEquals(1, DemoFeed.like(post.id, true).ok().likesCount)
        val unliked = DemoFeed.like(post.id, false).ok()
        assertFalse(unliked.liked)
        assertEquals(0, unliked.likesCount)
    }

    @Test
    fun editOnlyOwn() {
        val post = DemoFeed.create("Было", null, "followers", profilePublic = false).ok().post
        val edited = DemoFeed.edit(post.id, "Стало").ok()
        assertEquals("Стало", edited.text)
        assertTrue(edited.edited)
        // запись 1 — чужая (Анна)
        DemoFeed.edit(1, "чужое").failure()
        DemoFeed.delete(1).failure()
    }

    @Test
    fun deleteRemovesFromFeed() {
        val post = DemoFeed.create("Удалю", null, "followers", profilePublic = false).ok().post
        DemoFeed.delete(post.id).ok()
        assertTrue(DemoFeed.feed("following").ok().items.none { it.id == post.id })
        DemoFeed.post(post.id).failure()
    }

    @Test
    fun commentsNewestFirstAndCounted() {
        val post = DemoFeed.create("Обсудим", null, "followers", profilePublic = false).ok().post
        val first = DemoFeed.addComment(post.id, "первый").ok()
        val second = DemoFeed.addComment(post.id, "второй").ok()
        val list = DemoFeed.comments(post.id).ok().items
        assertEquals(listOf(second.id, first.id), list.map { it.id })
        assertEquals(2, DemoFeed.post(post.id).ok().commentsCount)
        DemoFeed.deleteComment(post.id, first.id).ok()
        assertEquals(1, DemoFeed.post(post.id).ok().commentsCount)
        DemoFeed.addComment(post.id, "  ").failure()
    }

    @Test
    fun followToggles() {
        val before = DemoFeed.followInfo(3)
        val followed = DemoFeed.follow(3, true).ok()
        assertTrue(followed.isFollowing)
        assertEquals(DemoFeed.feed("following").ok().items.any { it.author.id == 3 }, true)
        val unfollowed = DemoFeed.follow(3, false).ok()
        assertFalse(unfollowed.isFollowing)
        assertEquals(before.followersCount - (if (before.isFollowing) 1 else 0), unfollowed.followersCount)
    }

    @Test
    fun cannotFollowSelf() {
        assertFalse(DemoFeed.followInfo(1).canFollow)
        DemoFeed.follow(1, true).failure()
    }

    @Test
    fun cannotReportOwnPostTwiceOrWithoutReason() {
        val own = DemoFeed.create("Своя", null, "followers", profilePublic = false).ok().post
        DemoFeed.report(own.id, null, "spam", false).failure()
        DemoFeed.report(4, null, "", false).failure()
        DemoFeed.report(4, null, REPORT_REASONS.first().first, false).ok()
        DemoFeed.report(4, null, REPORT_REASONS.first().first, false).failure()
    }
}
