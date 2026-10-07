package ru.angelhelper.app.data

import org.junit.Assert.fail

/** Значение успешного ответа; при отказе — падение теста с текстом отказа. */
fun <T> Outcome<T>.ok(): T = when (this) {
    is Outcome.Ok -> value
    is Outcome.Fail -> {
        fail("Ожидался успех, а пришло: $message")
        throw IllegalStateException()
    }
}

/** Текст отказа; при успехе — падение теста. */
fun Outcome<*>.failure(): String = when (this) {
    is Outcome.Fail -> message
    is Outcome.Ok -> {
        fail("Ожидался отказ, а пришло: $value")
        throw IllegalStateException()
    }
}
