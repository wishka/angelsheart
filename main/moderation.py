"""
Правила допуска сбора средств к публикации.

До этого модуля модерации не было вовсе: любой пользователь мгновенно
публиковал сбор на 10 000 000 ₽ «на лечение» без единого документа и без
какой-либо проверки. Для площадки это прямой риск по ст. 159 УК — сбор
средств под ложным предлогом с её витрины, — и гражданские требования
жертвователей.

Здесь только автоматические проверки: они отсеивают очевидно негодные
заявки до того, как их увидит человек. Решение принимает модератор,
автоматика его не заменяет.
"""

import re
from decimal import Decimal

from django.conf import settings

from main.models import Fundraise, UserConsent


class ModerationError(Exception):
    """Сбор не может быть отправлен на проверку."""


# Категории, где одних слов автора мало: заявление о болезни или
# официальном обстоятельстве требует подтверждения документом.
CATEGORIES_REQUIRING_DOCUMENTS = {'medical', 'education'}

# Стоп-слова: не запрет, а повод показать заявку модератору отдельно.
# Список намеренно короткий — широкий фильтр даёт ложные срабатывания
# и создаёт ощущение, будто проверка автоматическая.
SUSPICIOUS_PATTERNS = [
    (re.compile(r'\b(крипт|битко|bitcoin|usdt|форекс|forex)\w*', re.I), 'криптовалюта или форекс'),
    (re.compile(r'\b(инвестиц|доход|прибыл|процент\w* годовых)\w*', re.I), 'обещание дохода'),
    (re.compile(r'\b(ставк|казино|букмекер|лотере)\w*', re.I), 'азартные игры'),
    (re.compile(r'\b(долг|кредит|микрозайм|займ)\w*', re.I), 'погашение долгов'),
    (re.compile(r'(https?://|www\.)', re.I), 'внешние ссылки'),
    (re.compile(r'\b(карт[аыу]|сбербанк|тинькофф|киви|qiwi)\b.{0,20}\d{4}', re.I),
     'реквизиты в обход сервиса'),
]


def required_verification_level(target_amount):
    """
    Уровень верификации, необходимый для заявленной суммы.

    Чем больше сумма, тем выше цена ошибки: анонимный сбор на миллион —
    это то, ради чего площадками пользуются мошенники.
    """
    amount = Decimal(target_amount)
    if amount > settings.FUNDRAISE_FULL_VERIFICATION_THRESHOLD:
        return 'full'
    if amount > settings.FUNDRAISE_BASIC_VERIFICATION_THRESHOLD:
        return 'basic'
    return 'unverified'


LEVEL_ORDER = {'unverified': 0, 'basic': 1, 'full': 2}
LEVEL_NAMES = {
    'unverified': 'без верификации',
    'basic': 'базовая верификация',
    'full': 'полная верификация',
}


def check_can_submit(fundraise):
    """
    Проверка перед отправкой сбора на модерацию.

    Возвращает список проблем, которые автор обязан устранить сам.
    Пустой список означает, что заявку можно передать модератору.
    """
    from main.models import EmailConfirmation
    from main.payments.verification import KYCService

    problems = []
    author = fundraise.author

    # 0. Подтверждённый адрес почты: на него уходит решение модератора,
    #    и по нему же автор восстанавливает доступ к учётной записи,
    #    на которую поступают деньги жертвователей
    if not EmailConfirmation.is_email_confirmed(author):
        problems.append(
            'Подтвердите адрес электронной почты — письмо со ссылкой можно '
            'запросить заново в профиле.'
        )

    # 1. Согласие на распространение: публикация делает имя автора
    #    и описание доступными неограниченному кругу лиц
    if not UserConsent.has_active_consent(author, 'distribution'):
        problems.append(
            'Нужно согласие на распространение персональных данных — '
            'без него сбор нельзя опубликовать.'
        )

    # 2. Уровень верификации под заявленную сумму
    required = required_verification_level(fundraise.target_amount)
    current = KYCService.get_level(author)
    if LEVEL_ORDER[current] < LEVEL_ORDER[required]:
        problems.append(
            f'Для сбора на {fundraise.target_amount:.0f} ₽ нужна '
            f'{LEVEL_NAMES[required]}, у вас — {LEVEL_NAMES[current]}. '
            f'Пройдите верификацию в разделе «Верификация».'
        )

    # 3. Подтверждающие документы для чувствительных категорий
    if fundraise.category in CATEGORIES_REQUIRING_DOCUMENTS:
        if not fundraise.documents.exists():
            problems.append(
                f'Для категории «{fundraise.get_category_display()}» нужно приложить '
                f'документ, подтверждающий цель сбора.'
            )

    # 4. Содержательность описания
    description = (fundraise.description or '').strip()
    if len(description) < settings.FUNDRAISE_MIN_DESCRIPTION_LENGTH:
        problems.append(
            f'Опишите цель подробнее — не менее '
            f'{settings.FUNDRAISE_MIN_DESCRIPTION_LENGTH} символов. '
            f'Жертвователи должны понимать, на что идут деньги.'
        )

    # 5. Долг перед жертвователями по прошлым сборам
    unpaid = Fundraise.objects.filter(
        author=author, status='cancelled',
        donations__refunded_at__isnull=True,
    ).exclude(pk=fundraise.pk).exists()
    if unpaid:
        problems.append(
            'По вашему прошлому отменённому сбору остался невозвращённый долг '
            'перед жертвователями. Погасите его, прежде чем открывать новый сбор.'
        )

    return problems


def detect_flags(fundraise):
    """
    Признаки, на которые модератору стоит обратить внимание.

    Не блокируют отправку: это подсказка человеку, а не автоматический отказ.
    """
    text = f'{fundraise.title}\n{fundraise.description}'
    flags = []

    for pattern, label in SUSPICIOUS_PATTERNS:
        if pattern.search(text):
            flags.append(label)

    if fundraise.target_amount >= settings.FUNDRAISE_LARGE_AMOUNT:
        flags.append(f'крупная сумма — {fundraise.target_amount:.0f} ₽')

    author_age_days = (fundraise.created_at - fundraise.author.date_joined).days \
        if fundraise.created_at else 0
    if author_age_days < 3:
        flags.append('учётная запись создана менее 3 дней назад')

    previous_cancelled = Fundraise.objects.filter(
        author=fundraise.author, status='cancelled',
    ).exclude(pk=fundraise.pk).count()
    if previous_cancelled:
        flags.append(f'ранее отменённых сборов: {previous_cancelled}')

    return flags
