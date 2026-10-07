package ru.angelhelper.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.UnknownHostException

/**
 * Обращения к Django по HTTP.
 *
 * Без сторонних библиотек: HttpURLConnection входит в платформу, org.json
 * тоже. Это заметно многословнее Retrofit, зато сборка не зависит ни от
 * одной внешней версии — для приложения, которое собирают на чужой
 * машине, это важнее краткости.
 *
 * Все вызовы уходят на Dispatchers.IO: сеть в главном потоке на Android
 * запрещена и роняет приложение с NetworkOnMainThreadException.
 */
class HttpApi(private val prefs: AppPrefs) : Api {

    // ==================== НИЗКИЙ УРОВЕНЬ ====================

    private data class Reply(val code: Int, val body: String)

    private fun urlFor(path: String): URL = URL(prefs.baseUrl + path)

    private fun call(
        path: String,
        method: String,
        payload: JSONObject?,
        token: String?,
    ): Reply {
        val connection = urlFor(path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            // Без таймаутов запрос к недоступному серверу висит минутами,
            // и приложение выглядит зависшим, а не «сервер не отвечает»
            connection.connectTimeout = 10_000
            connection.readTimeout = 20_000
            connection.setRequestProperty("Accept", "application/json")
            if (token != null) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            if (payload != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            }

            val code = connection.responseCode
            // При коде 4xx и 5xx inputStream бросает исключение, тело
            // лежит в errorStream — а именно там сервер объясняет отказ
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use(BufferedReader::readText) ?: ""
            return Reply(code, body)
        } finally {
            connection.disconnect()
        }
    }

    /**
     * Запрос с обновлением токена доступа.
     *
     * Токен SimpleJWT живёт недолго. Без этой попытки приложение
     * выбрасывало бы человека на экран входа посреди работы — и, что
     * хуже, ровно в тот момент, когда он нажал «Перевести».
     */
    private fun authorized(path: String, method: String, payload: JSONObject?): Reply {
        val first = call(path, method, payload, prefs.accessToken)
        if (first.code != 401) return first

        val refresh = prefs.refreshToken ?: return first
        val refreshed = call(
            "api/auth/refresh/", "POST", JSONObject().put("refresh", refresh), null,
        )
        if (refreshed.code !in 200..299) return first

        val json = JSONObject(refreshed.body)
        val access = json.optString("access", "")
        if (access.isEmpty()) return first
        prefs.accessToken = access

        // На сервере включены ROTATE_REFRESH_TOKENS и
        // BLACKLIST_AFTER_ROTATION: вместе с новым access приходит новый
        // refresh, а прежний заносится в чёрный список. Не сохранив его,
        // приложение обновляет токен ровно один раз, а через час
        // получает 401 на каждом экране — и выглядит это как поломка
        // сервера, а не как просроченный вход.
        val refreshedToken = json.optString("refresh", "")
        if (refreshedToken.isNotEmpty()) prefs.refreshToken = refreshedToken

        return call(path, method, payload, access)
    }

    /**
     * Человеческое сообщение об отказе.
     *
     * DRF отвечает по-разному: {"error": "..."} из наших вью,
     * {"detail": "..."} из самого DRF и {"поле": ["сообщение"]} из
     * проверки формы. Показать нужно то, что написано, — сервер уже
     * объяснил причину по-русски, и заменять это на «ошибка запроса»
     * значит выбросить единственное полезное, что есть.
     */
    /**
     * Особый текст для случая, когда вход больше не действует.
     *
     * Модель узнаёт его и выбрасывает человека на экран входа. Без этого
     * приложение считало его вошедшим и показывало 401 на каждом экране,
     * а уйти со сломанного экрана было некуда.
     */
    private fun sessionExpired(): Outcome<Nothing> = Outcome.Fail(SESSION_EXPIRED)

    /**
     * Отказ по ответу, полученному через [authorized].
     *
     * 401 здесь означает, что обновить токен не удалось, — то есть вход
     * больше не действует. На экране входа 401 значит совсем другое
     * («неверный пароль»), поэтому там эта функция не применяется.
     */
    private fun failure(reply: Reply): Outcome<Nothing> =
        if (reply.code == 401) sessionExpired() else Outcome.Fail(errorText(reply))

    private fun errorText(reply: Reply): String {
        if (reply.body.isBlank()) return "Сервер ответил кодом ${reply.code}"
        return try {
            val json = JSONObject(reply.body)
            json.optString("error").takeIf { it.isNotEmpty() }
                ?: json.optString("detail").takeIf { it.isNotEmpty() }
                ?: buildString {
                    val keys = json.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val value = json.get(key)
                        val text = if (value is JSONArray && value.length() > 0) {
                            value.optString(0)
                        } else {
                            value.toString()
                        }
                        if (isNotEmpty()) append('\n')
                        append(text)
                    }
                }.takeIf { it.isNotEmpty() }
                ?: "Сервер ответил кодом ${reply.code}"
        } catch (_: Exception) {
            "Сервер ответил кодом ${reply.code}"
        }
    }

    /**
     * Общая обёртка: ловит сетевые сбои и превращает их в понятный текст.
     *
     * «Сервер недоступен» на телефоне почти всегда означает одно из двух:
     * забыли `adb reverse` или сервер слушает не тот адрес. Подсказка
     * прямо в сообщении экономит вечер.
     */
    private suspend fun <T> request(block: () -> Outcome<T>): Outcome<T> =
        withContext(Dispatchers.IO) {
            try {
                block()
            } catch (_: UnknownHostException) {
                Outcome.Fail("Не удалось найти сервер по адресу ${prefs.baseUrl}")
            } catch (exception: IOException) {
                Outcome.Fail(
                    "Сервер по адресу ${prefs.baseUrl} не отвечает.\n\n" +
                        "На телефоне localhost — это сам телефон. Нужен " +
                        "`adb reverse tcp:8000 tcp:8000`, либо адрес машины " +
                        "в локальной сети. В эмуляторе — 10.0.2.2.\n\n" +
                        (exception.message ?: "")
                )
            } catch (exception: Exception) {
                Outcome.Fail(exception.message ?: "Неизвестная ошибка")
            }
        }

    // ==================== РАЗБОР ОТВЕТОВ ====================

    /**
     * Список из ответа: DRF отдаёт его либо массивом, либо страницей
     * вида {count, results}. Приложение должно понимать оба, иначе
     * включение постраничного вывода на сервере молча опустошает экран.
     */
    private fun itemsOf(body: String): JSONArray {
        val trimmed = body.trim()
        if (trimmed.startsWith("[")) return JSONArray(trimmed)
        val json = JSONObject(trimmed)
        return json.optJSONArray("results") ?: JSONArray()
    }

    /**
     * Сумма из ответа — всегда две цифры после запятой.
     *
     * Сервер отдаёт деньги двумя разными способами. Поля сериализаторов
     * (DecimalField) приходят строкой «12500.50». А вью статистики и
     * рейтинга собирают ответ руками, и сырой Decimal превращается в
     * JSON-число: JSONObject отдаёт его как Double, и toString() рисует
     * «12500.5», «0» вместо «0.00» и — начиная с десяти миллионов —
     * «1.0E7». Один и тот же баланс выглядел на «Главной» и в «Профиле»
     * по-разному.
     *
     * BigDecimal из строкового представления (не из Double!) и
     * toPlainString решают оба: и точность, и научную запись.
     */
    private fun money(json: JSONObject, key: String): String {
        val raw = json.opt(key) ?: return "0.00"
        val text = raw.toString()
        if (text.isBlank() || text == "null") return "0.00"
        return try {
            java.math.BigDecimal(text)
                .setScale(2, java.math.RoundingMode.HALF_UP)
                .toPlainString()
        } catch (_: NumberFormatException) {
            text
        }
    }

    private fun txOf(json: JSONObject) = Tx(
        id = json.optInt("id"),
        senderName = json.optString("sender_name", ""),
        receiverName = json.optString("receiver_name", ""),
        amount = money(json, "amount"),
        comment = json.optString("comment", ""),
        createdAt = json.optString("created_at", ""),
        status = json.optString("status", ""),
        isDonation = json.optBoolean("is_donation", false),
    )

    private fun fundraiseOf(json: JSONObject): Fundraise {
        // В списке автор приходит полем author_name, в карточке — вложенным
        // объектом author. Разбираем оба, иначе на карточке сбора имя
        // автора пропадает без всякой ошибки.
        val author = json.optJSONObject("author")?.optString("username")
            ?: json.optString("author_name", "")
        return Fundraise(
            id = json.optInt("id"),
            title = json.optString("title", ""),
            description = json.optString("description", ""),
            category = json.optString("category", "other"),
            targetAmount = money(json, "target_amount"),
            currentAmount = money(json, "current_amount"),
            progressPercent = json.optDouble("progress_percent", 0.0).toInt(),
            authorName = author,
            donorsCount = json.optInt("donors_count", 0),
            status = json.optString("status", ""),
            createdAt = json.optString("created_at", ""),
        )
    }

    // ==================== МЕТОДЫ ====================

    override suspend fun login(username: String, password: String, code: String): Outcome<Tokens> =
        request {
            val payload = JSONObject()
                .put("username", username)
                .put("password", password)
            if (code.isNotBlank()) payload.put("code", code)

            val reply = call("api/auth/login/", "POST", payload, null)
            // 403 с code_required — у человека включена двухфакторная
            // проверка. Экран по этому признаку сам раскрывает поле кода:
            // иначе нужно было догадаться нажать переключатель, о котором
            // никто не знает.
            if (reply.code == 403 && reply.body.contains("code_required")) {
                return@request Outcome.Fail(TWO_FACTOR_REQUIRED)
            }
            if (reply.code !in 200..299) return@request Outcome.Fail(errorText(reply))

            val json = JSONObject(reply.body)
            prefs.username = json.optJSONObject("user")?.optString("username") ?: username
            Outcome.Ok(
                Tokens(
                    access = json.optString("access"),
                    refresh = json.optString("refresh"),
                )
            )
        }

    override suspend fun register(
        username: String,
        email: String,
        password: String,
        acceptTerms: Boolean,
        consentDataProcessing: Boolean,
    ): Outcome<Tokens> = request {
        val payload = JSONObject()
            .put("username", username)
            .put("email", email)
            .put("password", password)
            .put("password2", password)
            .put("accept_terms", acceptTerms)
            .put("consent_data_processing", consentDataProcessing)
            .put("consent_distribution", false)

        val reply = call("api/auth/register/", "POST", payload, null)
        if (reply.code !in 200..299) return@request Outcome.Fail(errorText(reply))

        // Сервер сразу выдаёт токены — берём их. Прежняя версия их
        // выбрасывала и отправляла человека набирать тот же пароль
        // второй раз, расходуя вдобавок квоту в десять входов за час.
        val json = JSONObject(reply.body)
        prefs.username = json.optJSONObject("user")?.optString("username") ?: username
        Outcome.Ok(
            Tokens(
                access = json.optString("access"),
                refresh = json.optString("refresh"),
            )
        )
    }

    override suspend fun logout(): Outcome<Unit> = request {
        val refresh = prefs.refreshToken
        if (refresh.isNullOrBlank()) return@request Outcome.Ok(Unit)
        // Ответ не проверяем: токены с телефона стираются в любом случае,
        // а неудача отзыва на сервере — не повод оставить человека внутри
        authorized("api/auth/logout/", "POST", JSONObject().put("refresh", refresh))
        Outcome.Ok(Unit)
    }

    override suspend fun me(): Outcome<Profile> = request {
        val reply = authorized("api/users/me/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val json = JSONObject(reply.body)
        Outcome.Ok(
            Profile(
                id = json.optInt("id"),
                username = json.optString("username", ""),
                email = json.optString("email", ""),
                balance = money(json, "balance"),
                verificationLevel = json.optString("verification_level", "unverified"),
                dateJoined = json.optString("date_joined", ""),
            )
        )
    }

    override suspend fun dashboard(): Outcome<Dashboard> = request {
        val reply = authorized("api/dashboard/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val json = JSONObject(reply.body)
        val recent = json.optJSONArray("recent_transactions") ?: JSONArray()
        val list = ArrayList<Tx>(recent.length())
        for (index in 0 until recent.length()) {
            list.add(txOf(recent.getJSONObject(index)))
        }
        Outcome.Ok(
            Dashboard(
                balance = money(json, "balance"),
                totalSent = money(json, "total_sent"),
                totalReceived = money(json, "total_received"),
                recent = list,
            )
        )
    }

    override suspend fun fundraises(search: String): Outcome<List<Fundraise>> = request {
        val path = if (search.isBlank()) {
            "api/fundraises/"
        } else {
            "api/fundraises/?search=" + java.net.URLEncoder.encode(search, "UTF-8")
        }
        val reply = authorized(path, "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val items = itemsOf(reply.body)
        val list = ArrayList<Fundraise>(items.length())
        for (index in 0 until items.length()) {
            list.add(fundraiseOf(items.getJSONObject(index)))
        }
        Outcome.Ok(list)
    }

    override suspend fun fundraise(id: Int): Outcome<Fundraise> = request {
        val reply = authorized("api/fundraises/$id/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)
        Outcome.Ok(fundraiseOf(JSONObject(reply.body)))
    }

    override suspend fun donate(
        id: Int,
        amount: String,
        message: String,
        anonymous: Boolean,
    ): Outcome<String> = request {
        val payload = JSONObject()
            .put("amount", amount)
            .put("message", message)
            .put("is_anonymous", anonymous)

        val reply = authorized("api/fundraises/$id/donate/", "POST", payload)
        if (reply.code !in 200..299) return@request failure(reply)

        val json = JSONObject(reply.body)
        val commission = json.optString("commission", "0.00")
        Outcome.Ok(
            buildString {
                append(json.optString("message", "Пожертвование отправлено"))
                if (commission != "0.00" && commission.isNotEmpty()) {
                    append("\nУдержана комиссия: $commission ₽")
                }
            }
        )
    }

    override suspend fun transfer(
        receiver: String,
        amount: String,
        comment: String,
    ): Outcome<String> = request {
        val payload = JSONObject()
            .put("receiver_username", receiver)
            .put("amount", amount)
            .put("comment", comment)

        val reply = authorized("api/transactions/transfer/", "POST", payload)
        if (reply.code !in 200..299) return@request failure(reply)

        val json = JSONObject(reply.body)
        Outcome.Ok("Перевод на ${money(json, "amount")} ₽ отправлен пользователю $receiver")
    }

    override suspend fun transactions(): Outcome<List<Tx>> = request {
        val reply = authorized("api/transactions/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val items = itemsOf(reply.body)
        val list = ArrayList<Tx>(items.length())
        for (index in 0 until items.length()) {
            list.add(txOf(items.getJSONObject(index)))
        }
        Outcome.Ok(list)
    }

    override suspend fun leaders(): Outcome<List<Leader>> = request {
        val reply = authorized("api/leaders/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val json = JSONObject(reply.body)
        val senders = json.optJSONArray("top_senders") ?: JSONArray()
        val list = ArrayList<Leader>(senders.length())
        for (index in 0 until senders.length()) {
            val item = senders.getJSONObject(index)
            list.add(
                Leader(
                    username = item.optString("username", ""),
                    amount = money(item, "total_sent"),
                )
            )
        }
        Outcome.Ok(list)
    }

    override suspend fun consents(): Outcome<List<Consent>> = request {
        val reply = authorized("api/consents/", "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)

        val items = itemsOf(reply.body)
        val list = ArrayList<Consent>(items.length())
        for (index in 0 until items.length()) {
            val item = items.getJSONObject(index)
            list.add(
                Consent(
                    id = item.optInt("id"),
                    title = item.optString("consent_type_display", item.optString("consent_type")),
                    version = item.optString("version", ""),
                    agreedAt = item.optString("agreed_at", ""),
                    accepted = item.optBoolean("is_accepted", false),
                )
            )
        }
        Outcome.Ok(list)
    }

    // ==================== СООБЩЕСТВО ====================
    // Сервер: приложение social в Django, пути /api/profile/, /api/people/,
    // /api/chats/, /api/groups/.

    private fun encode(value: String): String = java.net.URLEncoder.encode(value, "UTF-8")

    /** Путь с параметрами; пустые значения пропускаются. */
    private fun withQuery(path: String, params: List<Pair<String, String>>): String {
        val query = params.filter { it.second.isNotBlank() }
            .joinToString("&") { (key, value) -> "$key=${encode(value)}" }
        return if (query.isEmpty()) path else "$path?$query"
    }

    /**
     * Строка или пусто. optString отдаёт для JSON-null строку «null» —
     * без этой проверки на экране появлялся бы город «null».
     */
    private fun JSONObject.text(key: String): String =
        if (isNull(key)) "" else optString(key, "")

    private fun JSONObject.intOrNull(key: String): Int? =
        if (!has(key) || isNull(key)) null else optInt(key)

    private fun <T> parseList(array: JSONArray?, parse: (JSONObject) -> T): List<T> {
        if (array == null) return emptyList()
        val list = ArrayList<T>(array.length())
        for (index in 0 until array.length()) list.add(parse(array.getJSONObject(index)))
        return list
    }

    private fun interestOf(json: JSONObject) = Interest(
        slug = json.text("slug"),
        title = json.text("title"),
    )

    private fun personOf(json: JSONObject) = Person(
        id = json.optInt("id"),
        username = json.text("username"),
        displayName = json.text("display_name").ifBlank { json.text("username") },
        city = json.text("city"),
        age = json.intOrNull("age"),
        gender = json.text("gender"),
        about = json.text("about"),
        interests = parseList(json.optJSONArray("interests"), ::interestOf),
    )

    private fun socialProfileOf(json: JSONObject) = SocialProfile(
        username = json.text("username"),
        displayName = json.text("display_name"),
        city = json.text("city"),
        birthYear = json.intOrNull("birth_year"),
        gender = json.text("gender"),
        about = json.text("about"),
        interests = parseList(json.optJSONArray("interests"), ::interestOf),
        isDiscoverable = json.optBoolean("is_discoverable", false),
        distributionConsent = json.optBoolean("distribution_consent", false),
    )

    private fun messageOf(json: JSONObject) = ChatMessage(
        id = json.optLong("id"),
        senderName = json.text("sender_name"),
        text = json.text("text"),
        createdAt = json.text("created_at"),
        isMine = json.optBoolean("is_mine", false),
    )

    private fun memberOf(json: JSONObject) = ChatMember(
        id = json.optInt("id"),
        username = json.text("username"),
        displayName = json.text("display_name").ifBlank { json.text("username") },
    )

    private fun chatOf(json: JSONObject) = ChatInfo(
        id = json.optInt("id"),
        kind = json.text("kind"),
        title = json.text("title"),
        membersCount = json.optInt("members_count"),
        peerUsername = json.optJSONObject("peer")?.text("username"),
        communityId = json.intOrNull("community_id"),
        lastMessage = json.optJSONObject("last_message")?.let(::messageOf),
        unreadCount = json.optInt("unread_count"),
        lastActivityAt = json.text("last_activity_at"),
        members = parseList(json.optJSONArray("members"), ::memberOf),
    )

    private fun communityOf(json: JSONObject) = Community(
        id = json.optInt("id"),
        name = json.text("name"),
        description = json.text("description"),
        topic = json.optJSONObject("topic")?.let(::interestOf),
        isPrivate = json.optBoolean("is_private", false),
        membersCount = json.optInt("members_count"),
        ownerUsername = json.text("owner_username"),
        myStatus = json.text("my_status").ifBlank { "none" },
        chatId = json.intOrNull("chat_id"),
        members = parseList(json.optJSONArray("members")) { item ->
            CommunityMember(
                id = item.optInt("id"),
                username = item.text("username"),
                displayName = item.text("display_name").ifBlank { item.text("username") },
                role = item.text("role"),
            )
        },
        pendingRequests = parseList(json.optJSONArray("pending_requests"), ::memberOf),
    )

    /** GET со списком: массив или страница {results} — itemsOf понимает оба. */
    private fun <T> getList(path: String, parse: (JSONObject) -> T): Outcome<List<T>> {
        val reply = authorized(path, "GET", null)
        if (reply.code !in 200..299) return failure(reply)
        return Outcome.Ok(parseList(itemsOf(reply.body), parse))
    }

    private fun <T> getOne(path: String, method: String, payload: JSONObject?, parse: (JSONObject) -> T): Outcome<T> {
        val reply = authorized(path, method, payload)
        if (reply.code !in 200..299) return failure(reply)
        return Outcome.Ok(parse(JSONObject(reply.body)))
    }

    override suspend fun interests(): Outcome<List<Interest>> = request {
        getList("api/interests/", ::interestOf)
    }

    override suspend fun socialProfile(): Outcome<SocialProfile> = request {
        getOne("api/profile/", "GET", null, ::socialProfileOf)
    }

    override suspend fun saveSocialProfile(profile: SocialProfile): Outcome<SocialProfile> = request {
        val interests = JSONArray()
        profile.interests.forEach { interests.put(it.slug) }
        val payload = JSONObject()
            .put("display_name", profile.displayName)
            .put("city", profile.city)
            .put("birth_year", profile.birthYear ?: JSONObject.NULL)
            .put("gender", profile.gender)
            .put("about", profile.about)
            .put("interests", interests)
            .put("is_discoverable", profile.isDiscoverable)
        // PUT, а не PATCH: HttpURLConnection не знает метода PATCH и бросает
        // ProtocolException. Сервер принимает PUT как частичное обновление.
        getOne("api/profile/", "PUT", payload, ::socialProfileOf)
    }

    override suspend fun people(filter: PeopleFilter): Outcome<List<Person>> = request {
        val path = withQuery(
            "api/people/",
            listOf(
                "q" to filter.query.trim(),
                "city" to filter.city.trim(),
                "gender" to filter.gender,
                "age_min" to (filter.ageMin?.toString() ?: ""),
                "age_max" to (filter.ageMax?.toString() ?: ""),
                "interests" to filter.interests.joinToString(","),
            ),
        )
        getList(path, ::personOf)
    }

    override suspend fun person(id: Int): Outcome<Person> = request {
        getOne("api/people/$id/", "GET", null, ::personOf)
    }

    override suspend fun chats(): Outcome<List<ChatInfo>> = request {
        getList("api/chats/", ::chatOf)
    }

    override suspend fun chat(id: Int): Outcome<ChatInfo> = request {
        getOne("api/chats/$id/", "GET", null, ::chatOf)
    }

    override suspend fun openChat(usernames: List<String>, title: String): Outcome<ChatInfo> = request {
        val names = JSONArray()
        usernames.forEach { names.put(it) }
        getOne("api/chats/", "POST", JSONObject().put("usernames", names).put("title", title), ::chatOf)
    }

    override suspend fun messages(chatId: Int, afterId: Long?): Outcome<List<ChatMessage>> = request {
        val path = withQuery("api/chats/$chatId/messages/", listOf("after" to (afterId?.toString() ?: "")))
        val reply = authorized(path, "GET", null)
        if (reply.code !in 200..299) return@request failure(reply)
        Outcome.Ok(parseList(JSONObject(reply.body).optJSONArray("results"), ::messageOf))
    }

    override suspend fun sendMessage(chatId: Int, text: String): Outcome<ChatMessage> = request {
        getOne("api/chats/$chatId/messages/", "POST", JSONObject().put("text", text), ::messageOf)
    }

    override suspend fun leaveChat(chatId: Int): Outcome<String> = request {
        getOne("api/chats/$chatId/leave/", "POST", JSONObject()) { it.text("message") }
    }

    override suspend fun communities(search: String, mineOnly: Boolean): Outcome<List<Community>> = request {
        val path = withQuery(
            "api/groups/",
            listOf("search" to search.trim(), "mine" to if (mineOnly) "1" else ""),
        )
        getList(path, ::communityOf)
    }

    override suspend fun community(id: Int): Outcome<Community> = request {
        getOne("api/groups/$id/", "GET", null, ::communityOf)
    }

    override suspend fun createCommunity(
        name: String,
        description: String,
        topic: String,
        isPrivate: Boolean,
    ): Outcome<Community> = request {
        val payload = JSONObject()
            .put("name", name)
            .put("description", description)
            .put("topic", topic)
            .put("is_private", isPrivate)
        getOne("api/groups/", "POST", payload, ::communityOf)
    }

    override suspend fun communityAction(id: Int, action: String, userId: Int?): Outcome<CommunityReply> =
        request {
            val payload = JSONObject()
            if (userId != null) payload.put("user_id", userId)
            getOne("api/groups/$id/$action/", "POST", payload) { json ->
                CommunityReply(communityOf(json), json.text("message"))
            }
        }

    // ==================== ЗАГЛУШКИ ====================
    // Этих точек в серверном API нет. Реализации совпадают с демо-режимом
    // намеренно: пусть отказ выглядит одинаково, где бы ни работало
    // приложение, и пусть нигде не создаётся впечатления, будто деньги
    // действительно пришли.

    override suspend fun topUp(amount: String, method: String): Outcome<String> =
        Demo.topUpReply(amount)

    override suspend fun withdraw(
        amount: String,
        method: String,
        target: String,
    ): Outcome<String> = Demo.withdrawReply(amount)

    override suspend fun submitVerification(
        fullName: String,
        birthDate: String,
        series: String,
        number: String,
    ): Outcome<String> = Demo.verificationReply()

    override suspend fun resetPassword(email: String): Outcome<String> =
        Demo.passwordResetReply(email)
}
