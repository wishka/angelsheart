package ru.angelhelper.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DemoSocialTest {

    /** Новый личный чат с Дмитрием (id 7) и одно своё сообщение в нём. */
    private fun chatWithMessage(text: String): Pair<Int, ChatMessage> {
        val chat = DemoSocial.openChat(listOf("dmitry_photo"), "").ok()
        return chat.id to DemoSocial.send(chat.id, text).ok()
    }

    @Test
    fun editMessageMarksEditedAndShowsInChanges() {
        val (chatId, message) = chatWithMessage("опечатко")
        val since = DemoSocial.messages(chatId, null).ok().serverTime
        val edited = DemoSocial.editMessage(chatId, message.id, "опечатка").ok()
        assertTrue(edited.edited)
        assertEquals("опечатка", edited.text)

        // Опрос после последнего сообщения: нового нет, но правка приходит
        val update = DemoSocial.messages(chatId, message.id, since).ok()
        assertTrue(update.items.isEmpty())
        assertEquals(listOf(message.id), update.changed.map { it.id })
    }

    @Test
    fun deleteMessageKeepsPlaceholder() {
        val (chatId, message) = chatWithMessage("лишнее")
        val deleted = DemoSocial.deleteMessage(chatId, message.id).ok()
        assertTrue(deleted.deleted)
        assertFalse(deleted.canEdit)
        // удалённое больше не правится
        DemoSocial.editMessage(chatId, message.id, "вернуть").failure()
    }

    @Test
    fun cannotEditForeignMessage() {
        // Чат 1: первое сообщение — от Анны
        val foreign = DemoSocial.messages(1, null).ok().items.first { !it.isMine }
        DemoSocial.editMessage(1, foreign.id, "взлом").failure()
        DemoSocial.deleteMessage(1, foreign.id).failure()
    }

    @Test
    fun privateGroupApproveAndRoles() {
        val group = DemoSocial.create("Тестовая закрытая", "", "", isPrivate = true).ok()
        assertEquals("owner", group.myStatus)
        val request = group.pendingRequests.single()

        val approved = DemoSocial.action(group.id, "approve", request.id).ok().community
        assertTrue(approved.members.any { it.id == request.id && it.role == "member" })

        val promoted = DemoSocial.action(group.id, "role", request.id, "admin").ok().community
        assertEquals("admin", promoted.members.first { it.id == request.id }.role)

        val demoted = DemoSocial.action(group.id, "role", request.id, "member").ok().community
        assertEquals("member", demoted.members.first { it.id == request.id }.role)
    }

    @Test
    fun transferOwnershipMakesMeAdmin() {
        val group = DemoSocial.create("Передача", "", "", isPrivate = true).ok()
        val newOwner = group.pendingRequests.single()
        DemoSocial.action(group.id, "approve", newOwner.id).ok()

        val after = DemoSocial.action(group.id, "transfer", newOwner.id).ok().community
        assertEquals("admin", after.myStatus)
        assertEquals(newOwner.username, after.ownerUsername)

        // теперь удалить группу и передать её снова нельзя
        DemoSocial.delete(group.id).failure()
        DemoSocial.action(group.id, "transfer", newOwner.id).failure()
        // и исключить владельца тоже
        DemoSocial.action(group.id, "remove", newOwner.id).failure()
    }

    @Test
    fun removeMemberAndDeleteGroup() {
        val group = DemoSocial.create("Исключение", "", "", isPrivate = true).ok()
        val member = group.pendingRequests.single()
        DemoSocial.action(group.id, "approve", member.id).ok()
        val after = DemoSocial.action(group.id, "remove", member.id).ok().community
        assertTrue(after.members.none { it.id == member.id })

        DemoSocial.update(group.id, "Новое имя", "описание", "", isPrivate = false).ok().let {
            assertEquals("Новое имя", it.name)
            assertFalse(it.isPrivate)
        }
        DemoSocial.delete(group.id).ok()
        DemoSocial.community(group.id).failure()
    }

    @Test
    fun ownerCannotLeave() {
        val group = DemoSocial.create("Не уйти", "", "", isPrivate = false).ok()
        DemoSocial.action(group.id, "leave", null).failure()
    }
}
