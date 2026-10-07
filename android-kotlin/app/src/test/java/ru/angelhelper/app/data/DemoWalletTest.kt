package ru.angelhelper.app.data

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Кошелёк демо-режима: ведёт себя как тестовый режим сервера.
 * runBlocking, а не runTest: пауз в демо немного, а лишняя тестовая
 * зависимость ради экономии пары секунд не нужна.
 */
class DemoWalletTest {

    private fun balanceOf(info: WalletInfo) = info.balance.replace(" ", "").replace(',', '.').toDouble()

    @Test
    fun topUpIsImmediate() = runBlocking<Unit> {
        val before = balanceOf(DemoApi.wallet().ok())
        val result = DemoApi.topUp("1000", "card").ok()
        assertTrue(result.completed)
        assertNull(result.confirmationUrl)
        assertEquals(before + 1000, balanceOf(DemoApi.wallet().ok()), 0.001)
        DemoApi.topUp("abc", "card").failure()
    }

    @Test
    fun withdrawalHoldsAndCancelReturns() = runBlocking<Unit> {
        DemoApi.topUp("5000", "card").ok()
        val before = balanceOf(DemoApi.wallet().ok())
        DemoApi.createWithdrawal(
            mapOf("payment_method" to "card", "amount" to "600", "card_number" to "2200 0000 0000 1234"),
        ).ok()
        assertEquals(before - 600, balanceOf(DemoApi.wallet().ok()), 0.001)

        val created = DemoApi.withdrawals().ok().first()
        assertTrue(created.canCancel)
        assertEquals("****1234", created.details)

        DemoApi.cancelWithdrawal(created.id).ok()
        assertEquals(before, balanceOf(DemoApi.wallet().ok()), 0.001)
        assertFalse(DemoApi.withdrawals().ok().first { it.id == created.id }.canCancel)
        DemoApi.cancelWithdrawal(created.id).failure()
    }

    @Test
    fun withdrawalLimits() = runBlocking<Unit> {
        DemoApi.createWithdrawal(mapOf("payment_method" to "card", "amount" to "100")).failure()
        DemoApi.createWithdrawal(mapOf("payment_method" to "card", "amount" to "999999")).failure()
        DemoApi.createWithdrawal(mapOf("payment_method" to "card", "amount" to "")).failure()
    }

    @Test
    fun verificationRules() = runBlocking<Unit> {
        DemoApi.submitVerification("Иванов", "01.01.1990", "4510", "123456").failure()
        DemoApi.submitVerification("Иванов Иван", "01.01.2015", "4510", "123456").failure()
        DemoApi.submitVerification("Иванов Иван", "01.01.1990", "45", "123456").failure()
        DemoApi.submitVerification("Иванов Иван", "01.01.1990", "4510", "123456").ok()
        assertTrue(DemoApi.verification().ok().submitted)

        DemoApi.uploadDocument("passport", byteArrayOf(1, 2, 3)).ok()
        assertTrue(DemoApi.verification().ok().documents.isNotEmpty())
    }

    @Test
    fun passwordResetNeedsEmail() = runBlocking<Unit> {
        DemoApi.resetPassword("not-an-email").failure()
        DemoApi.resetPassword("me@example.com").ok()
    }
}
