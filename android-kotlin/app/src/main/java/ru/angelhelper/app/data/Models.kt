package ru.angelhelper.app.data

/**
 * Данные, которыми обменивается приложение и сервер.
 *
 * Суммы хранятся строками, а не Double, намеренно: Double не умеет
 * точно представлять десятичные дроби, и «100.10 + 0.20» на экране
 * однажды превращается в «100.29999999999998». Складывать деньги
 * приложению не нужно вовсе — оно их показывает, а считает сервер.
 */

data class Tokens(
    val access: String,
    val refresh: String,
)

data class Profile(
    val id: Int,
    val username: String,
    val email: String,
    val balance: String,
    val verificationLevel: String,
    val dateJoined: String,
) {
    val verificationTitle: String
        get() = when (verificationLevel) {
            "basic" -> "Базовая"
            "full" -> "Полная"
            else -> "Не пройдена"
        }
}

data class Dashboard(
    val balance: String,
    val totalSent: String,
    val totalReceived: String,
    val recent: List<Tx>,
)

data class Tx(
    val id: Int,
    val senderName: String,
    val receiverName: String,
    val amount: String,
    val comment: String,
    val createdAt: String,
    val status: String,
    val isDonation: Boolean,
) {
    fun incomingFor(username: String): Boolean = receiverName == username

    val statusTitle: String
        get() = when (status) {
            "completed" -> "Завершён"
            "pending" -> "В обработке"
            "failed" -> "Не выполнен"
            "cancelled" -> "Отменён"
            else -> status
        }
}

data class Fundraise(
    val id: Int,
    val title: String,
    val description: String,
    val category: String,
    val targetAmount: String,
    val currentAmount: String,
    val progressPercent: Int,
    val authorName: String,
    val donorsCount: Int,
    val status: String,
    val createdAt: String,
) {
    // Список в точности как CATEGORY_CHOICES модели Fundraise на сервере.
    // Первая версия писала "animals" вместо "animal" и придумывала
    // "emergency" и "social", которых на сервере нет вовсе: почти каждый
    // сбор показывался как «Другое», и заметить это было нельзя — экран
    // выглядел исправным.
    val categoryTitle: String
        get() = when (category) {
            "medical" -> "Медицина и здоровье"
            "education" -> "Образование"
            "animal" -> "Помощь животным"
            "ecology" -> "Экология"
            "sport" -> "Спорт"
            "art" -> "Искусство и культура"
            "business" -> "Бизнес и стартапы"
            else -> "Другое"
        }
}

/**
 * Дата из ответа сервера в человеческом виде.
 *
 * DRF отдаёт «2026-09-14T10:15:00.123456Z». Показывать это как есть —
 * значит подписывать операцию машинным мусором.
 */
fun String.asReadableDate(): String {
    if (length < 10) return this
    val date = substring(0, 10)
    val parts = date.split("-")
    if (parts.size != 3) return this
    val time = if (length >= 16 && this[10] == 'T') " " + substring(11, 16) else ""
    return "${parts[2]}.${parts[1]}.${parts[0]}$time"
}

data class Leader(
    val username: String,
    val amount: String,
)

data class Consent(
    val id: Int,
    val title: String,
    val version: String,
    val agreedAt: String,
    val accepted: Boolean,
)

/**
 * Результат обращения к серверу.
 *
 * Отдельный тип вместо исключений: экран обязан показать причину отказа
 * человеку, а не «что-то пошло не так». Сообщение приходит с сервера как
 * есть — оно уже написано по-русски и объясняет, чего не хватило.
 */
sealed class Outcome<out T> {
    // out T и здесь тоже: без ковариантности `is Outcome.Ok` в обобщённой
    // функции даёт предупреждение о непроверяемом приведении, и вместо
    // типизированного результата приходится возиться с Any?
    data class Ok<out T>(val value: T) : Outcome<T>()
    data class Fail(val message: String) : Outcome<Nothing>()
}
