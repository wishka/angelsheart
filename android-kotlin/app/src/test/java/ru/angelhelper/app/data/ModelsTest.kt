package ru.angelhelper.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelsTest {

    @Test
    fun appendPageSkipsDuplicates() {
        val first = listOf(1, 2, 3)
        val merged = first.appendPage(listOf(3, 4, 4, 5)) { it }
        assertEquals(listOf(1, 2, 3, 4, 5), merged)
    }

    @Test
    fun appendPageKeepsOrderOfExisting() {
        data class Item(val id: Int, val title: String)
        val merged = listOf(Item(2, "b"), Item(1, "a")).appendPage(listOf(Item(1, "new"), Item(0, "z"))) { it.id }
        assertEquals(listOf("b", "a", "z"), merged.map { it.title })
    }

    @Test
    fun pluralRussian() {
        assertEquals("1 год", plural(1, "год", "года", "лет"))
        assertEquals("3 года", plural(3, "год", "года", "лет"))
        assertEquals("5 лет", plural(5, "год", "года", "лет"))
        assertEquals("11 лет", plural(11, "год", "года", "лет"))
        assertEquals("21 год", plural(21, "год", "года", "лет"))
        assertEquals("112 лет", plural(112, "год", "года", "лет"))
        assertEquals("0 подписчиков", plural(0, "подписчик", "подписчика", "подписчиков"))
    }

    @Test
    fun messageFlags() {
        val mine = ChatMessage(1, "Вы", "привет", "", isMine = true, senderId = 1)
        assertTrue(mine.canEdit)
        assertFalse(mine.canReport)
        assertFalse(mine.copy(deleted = true).canEdit)

        val other = ChatMessage(2, "Анна", "привет", "", isMine = false, senderId = 2)
        assertTrue(other.canReport)
        assertFalse(other.canEdit)
        assertFalse(other.copy(deleted = true).canReport)
        assertFalse(other.copy(isHidden = true).canReport)
    }

    @Test
    fun postVisibilityTitle() {
        val author = Author(1, "u", "U", false, "")
        val post = Post(1, author, "t", false, "public", "", false, true, false, 0, 0, false)
        assertEquals("Всем", post.visibilityTitle)
        assertEquals("Подписчикам", post.copy(visibility = "followers").visibilityTitle)
    }
}
