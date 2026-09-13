"""
Проверка домена адреса электронной почты при регистрации.

Адрес — единственный способ связаться с человеком: по нему уходит
уведомление о выплате, решение об ограничении счёта и ссылка
восстановления доступа. Поэтому имеет смысл отсеять два случая, которые
дают заведомо недоставляемый адрес:

* одноразовые почтовые ящики — живут десять минут, письмо о блокировке
  счёта через неделю прочитать уже невозможно;
* опечатки в популярных доменах (gmial.com, yadnex.ru) — человек уверен,
  что адрес указан верно, и узнаёт об ошибке, только когда понадобится
  восстановить пароль.

Проверка MX-записей здесь намеренно не делается: она требует сетевого
запроса при каждой регистрации и падает вместе с DNS, а выигрыш мал.
"""

from django.conf import settings

# Домены одноразовой почты. Список заведомо неполный — их тысячи, и новые
# появляются ежедневно. Задача не «поймать все», а не принимать очевидные;
# расширяется через DISPOSABLE_EMAIL_DOMAINS в окружении.
DISPOSABLE_DOMAINS = {
    '10minutemail.com', '10minutemail.net', 'temp-mail.org', 'tempmail.com',
    'tempmail.net', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com',
    'mailinator.com', 'maildrop.cc', 'dispostable.com', 'yopmail.com',
    'trashmail.com', 'throwawaymail.com', 'getnada.com', 'mohmal.com',
    'fakeinbox.com', 'tempr.email', 'discard.email', 'spamgourmet.com',
    'mytemp.email', 'emailondeck.com', 'tempmailo.com', 'minuteinbox.com',
    'temp-mail.io', 'mailnesia.com', 'inboxkitten.com', 'burnermail.io',
}

# Настоящие домены, похожие на популярные. Без этого списка проверка
# опечаток отвергала бы живых людей: mail.com и email.com принадлежат
# 1&1 Mail & Media, ymail.com — собственный домен Yahoo, и все три
# отличаются от gmail.com ровно одной буквой.
KNOWN_REAL_DOMAINS = {
    'mail.com', 'email.com', 'ymail.com', 'gmx.com', 'gmx.net', 'aol.com',
    'googlemail.com', 'yandex.by', 'yandex.kz', 'yandex.com', 'inbox.lv',
    'proton.me', 'pm.me', 'me.com', 'mac.com', 'msn.com', 'live.com',
    'vk.team', 'internet.ru', 'mail.ua', 'ukr.net', 'rambler.com',
}

# Домены, в которых чаще всего ошибаются. Проверяются на расстояние
# редактирования: одна опечатка — предупреждаем, две и больше — считаем,
# что домен другой, и не мешаем.
POPULAR_DOMAINS = [
    'gmail.com', 'yandex.ru', 'ya.ru', 'mail.ru', 'bk.ru', 'inbox.ru',
    'list.ru', 'rambler.ru', 'outlook.com', 'hotmail.com', 'icloud.com',
    'yahoo.com', 'protonmail.com', 'vk.com',
]


def _all_disposable():
    extra = {
        domain.strip().lower()
        for domain in getattr(settings, 'DISPOSABLE_EMAIL_DOMAINS', '').split(',')
        if domain.strip()
    }
    return DISPOSABLE_DOMAINS | extra


def _distance(first, second):
    """
    Расстояние Дамерау — Левенштейна: считает перестановку соседних букв
    за одну ошибку.

    Именно перестановка даёт самые частые опечатки в доменах — gmial.com
    вместо gmail.com, yadnex.ru вместо yandex.ru. Обычное расстояние
    Левенштейна оценивает их в две ошибки и пропускает.
    """
    if first == second:
        return 0
    if not first or not second:
        return len(first) or len(second)

    rows, columns = len(first) + 1, len(second) + 1
    matrix = [[0] * columns for _ in range(rows)]
    for i in range(rows):
        matrix[i][0] = i
    for j in range(columns):
        matrix[0][j] = j

    for i in range(1, rows):
        for j in range(1, columns):
            cost = 0 if first[i - 1] == second[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,        # удаление
                matrix[i][j - 1] + 1,        # вставка
                matrix[i - 1][j - 1] + cost, # замена
            )
            if (
                i > 1 and j > 1
                and first[i - 1] == second[j - 2]
                and first[i - 2] == second[j - 1]
            ):
                matrix[i][j] = min(matrix[i][j], matrix[i - 2][j - 2] + 1)

    return matrix[-1][-1]


# Короткие домены не проверяются на опечатки: у mail.ru и ya.ru слишком
# много настоящих соседей на расстоянии одной буквы, и предупреждение
# мешало бы людям с обычными адресами.
MIN_DOMAIN_LENGTH_FOR_TYPO_CHECK = 8


def check_email_domain(email, allow_typo=False):
    """
    Проверка домена. Возвращает текст ошибки или None.

    Сообщение об опечатке содержит предполагаемый верный домен: просто
    «неверный адрес» человеку ничего не объясняет — он смотрит на свой
    адрес и не видит в нём ошибки.

    Одноразовый домен отвергается всегда, а похожий на популярный —
    только один раз: при повторной отправке формы (allow_typo=True)
    адрес принимается. Иначе проверка опечаток превращается в запрет
    на регистрацию для тех, у кого домен действительно такой.
    """
    address = (email or '').strip().lower()
    if '@' not in address:
        return None  # формат проверяет сам Django

    domain = address.rsplit('@', 1)[1]
    if not domain:
        return None

    if domain in _all_disposable():
        return (
            'Одноразовые почтовые адреса не подходят: на этот адрес приходят '
            'уведомления об операциях и ссылка восстановления доступа. '
            'Укажите постоянный адрес.'
        )

    if domain in POPULAR_DOMAINS or domain in KNOWN_REAL_DOMAINS:
        return None

    if allow_typo:
        return None

    for popular in POPULAR_DOMAINS:
        if len(popular) < MIN_DOMAIN_LENGTH_FOR_TYPO_CHECK:
            continue
        if _distance(domain, popular) == 1:
            return (
                f'Возможно, в адресе опечатка: вы имели в виду @{popular}? '
                f'Если адрес указан верно, отправьте форму ещё раз — '
                f'он будет принят.'
            )

    return None
