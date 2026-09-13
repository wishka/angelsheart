"""
Денежные операции.

Раньше эта логика была скопирована в четырёх местах (вью перевода, вью
пожертвования, API-перевод, API-пожертвование) и везде отличалась: в одной
копии не хватало проверки баланса под блокировкой, в другой — вычислений
на float вместо Decimal, в третьей — комиссии. Здесь она одна.

Инвариант всех функций: проверка достаточности средств выполняется
**после** select_for_update(), внутри той же транзакции, что и списание.
Проверка до открытия транзакции ничего не гарантирует — между ней и
списанием проходит другой запрос.

Важно: на SQLite select_for_update() не создаёт блокировок, поэтому
защита от гонок здесь реальна только на PostgreSQL.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction as db_transaction

from main.models import (
    AccountRestriction, Balance, CommissionTransaction, Donation, Fundraise,
    Transaction,
)

logger = logging.getLogger('withdrawals')

KOPECK = Decimal('0.01')


class InsufficientFunds(Exception):
    """На балансе недостаточно средств."""


class OperationRejected(Exception):
    """Операция не разрешена бизнес-правилами."""


def quantize(amount):
    """Округление до копеек по правилу «половина вверх»."""
    return Decimal(amount).quantize(KOPECK, rounding=ROUND_HALF_UP)


def ensure_can_receive(user):
    """
    Получатель должен быть действующим пользователем.

    Иначе деньги уходят в никуда: на обезличенный аккаунт после удаления
    (владельца уже нет) или на служебный аккаунт сервиса. Оба видны
    в поиске получателей, и оба принимали переводы.
    """
    if not user.is_active:
        raise OperationRejected('Получатель не может принимать переводы')
    if user.username == settings.SERVICE_ACCOUNT_USERNAME:
        raise OperationRejected('Это служебный аккаунт сервиса, перевод на него невозможен')


def ensure_email_confirmed(user, action):
    """
    Расходная операция требует подтверждённого адреса почты.

    Смысл не в формальности: по этому адресу уходит уведомление о выплате
    и по нему же восстанавливается доступ к учётной записи. Опечатка
    в адресе означает, что вывести деньги можно, а узнать о судьбе выплаты
    или вернуть себе доступ — нет.
    """
    from main.models import EmailConfirmation

    if EmailConfirmation.is_email_confirmed(user):
        return

    raise OperationRejected(
        f'{action} недоступен, пока не подтверждён адрес электронной почты. '
        f'Письмо со ссылкой можно запросить заново в профиле.'
    )


def ensure_can_operate(user):
    """
    Расходные операции запрещены при действующем ограничении.

    Раздел 9 оферты описывает приостановление операций, но в коде его
    не было: «заблокировать» можно было только через is_active=False —
    без причины, без уведомления и без возможности возразить.

    Ограничение касается только расходных операций: п. 9.4 прямо говорит,
    что блокировка не влечёт утрату права на средства, поэтому баланс
    остаётся за пользователем и зачисления не блокируются.
    """
    restriction = AccountRestriction.active_for(user)
    if restriction is None:
        return

    raise OperationRejected(
        f'Операции по вашей учётной записи приостановлены: {restriction.reason} '
        f'Подать объяснения можно в разделе «Ограничения».'
    )


def _lock_balances(*users):
    """
    Блокировка балансов в детерминированном порядке.

    Порядок по user_id обязателен: если один запрос блокирует A потом B,
    а встречный B потом A, получается взаимная блокировка.
    """
    user_ids = sorted({user.id for user in users})
    balances = {
        balance.user_id: balance
        for balance in Balance.objects.select_for_update().filter(user_id__in=user_ids).order_by('user_id')
    }
    missing = set(user_ids) - set(balances)
    if missing:
        # Баланс создаётся сигналом при регистрации; его отсутствие — сбой данных
        raise OperationRejected('Баланс пользователя не найден')
    return balances


def _debit(balance, amount):
    if balance.amount < amount:
        raise InsufficientFunds(
            f'Недостаточно средств: на балансе {balance.amount} ₽, требуется {amount} ₽'
        )
    balance.amount -= amount
    balance.save(update_fields=['amount'])


def _credit(balance, amount):
    balance.amount += amount
    balance.save(update_fields=['amount'])


@db_transaction.atomic
def transfer(sender, receiver, amount, comment=''):
    """Перевод между пользователями."""
    amount = quantize(amount)
    if amount <= 0:
        raise OperationRejected('Сумма перевода должна быть больше нуля')
    if sender.pk == receiver.pk:
        raise OperationRejected('Нельзя перевести деньги самому себе')
    ensure_can_operate(sender)
    # Перевод — такое же распоряжение деньгами, как вывод: без этой
    # проверки неподтверждённый пользователь переводил весь баланс
    # на подтверждённую учётную запись и выводил оттуда.
    ensure_email_confirmed(sender, 'Перевод средств')
    ensure_can_receive(receiver)

    balances = _lock_balances(sender, receiver)
    _debit(balances[sender.pk], amount)
    _credit(balances[receiver.pk], amount)

    return Transaction.objects.create(
        sender=sender,
        receiver=receiver,
        amount=amount,
        comment=comment,
        status='completed',
        kind='transfer',
    )


def commission_for(amount):
    """Комиссия сервиса с пожертвования по ставке из настроек."""
    percent = Decimal(settings.DONATION_COMMISSION_PERCENT)
    if percent <= 0:
        return Decimal('0.00')
    return quantize(amount * percent / Decimal('100'))


def get_service_account():
    """
    Служебный аккаунт, на который зачисляется комиссия.

    Без него деньги не сходятся: донор списывался на полную сумму, автору
    зачислялось меньше, а разница просто исчезала из системы. Сумма всех
    балансов обязана меняться на ноль при любой внутренней операции.

    Создаётся командой `python manage.py create_service_account`.
    """
    from django.contrib.auth.models import User

    username = settings.SERVICE_ACCOUNT_USERNAME
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        raise OperationRejected(
            f'Служебный аккаунт «{username}» не найден. '
            f'Выполните: python manage.py create_service_account'
        )


@db_transaction.atomic
def donate(donor, fundraise, amount, message='', is_anonymous=False):
    """
    Пожертвование в сбор.

    Возвращает созданный Donation. Комиссия удерживается по единой ставке
    settings.DONATION_COMMISSION_PERCENT и, если она ненулевая, фиксируется
    записью CommissionTransaction — раньше удержанные деньги просто исчезали.
    """
    amount = quantize(amount)
    ensure_can_operate(donor)
    if amount < settings.MIN_DONATION_AMOUNT:
        raise OperationRejected(f'Минимальная сумма пожертвования — {settings.MIN_DONATION_AMOUNT} ₽')
    if amount > settings.MAX_DONATION_AMOUNT:
        raise OperationRejected(f'Максимальная сумма пожертвования — {settings.MAX_DONATION_AMOUNT} ₽')

    # Блокируем сам сбор: параллельные пожертвования иначе затирают
    # current_amount и donors_count друг друга (read-modify-write).
    fundraise = Fundraise.objects.select_for_update().get(pk=fundraise.pk)

    if fundraise.author_id == donor.pk:
        raise OperationRejected('Нельзя пожертвовать в собственный сбор')
    # Проверка стоит здесь, под блокировкой строки: это единственная точка,
    # через которую деньги попадают в сбор. Проверка во вью её не заменяет —
    # API и админка приходят сюда же.
    if not fundraise.accepts_donations:
        if fundraise.moderation_status != 'approved':
            raise OperationRejected('Сбор не прошёл проверку и пока не принимает пожертвования')
        if fundraise.is_expired:
            raise OperationRejected('Срок сбора истёк')
        raise OperationRejected('Сбор неактивен')

    author = fundraise.author
    ensure_can_receive(author)
    # Сбор автора с действующим ограничением денег не принимает: ограничение
    # применяется как раз тогда, когда есть подозрение в обмане, и продолжать
    # собирать в это время — собирать под риск, о котором сервис уже знает.
    if AccountRestriction.active_for(author):
        raise OperationRejected('Сбор временно не принимает пожертвования')
    commission = commission_for(amount)
    author_amount = amount - commission

    # Служебный аккаунт блокируется вместе с остальными: комиссия должна
    # куда-то зачисляться, иначе деньги пропадают из системы
    service_account = get_service_account() if commission > 0 else None
    participants = [donor, author] + ([service_account] if service_account else [])
    balances = _lock_balances(*participants)

    _debit(balances[donor.pk], amount)
    _credit(balances[author.pk], author_amount)
    if service_account:
        _credit(balances[service_account.pk], commission)

    # Счётчик считает людей, а не пожертвования: раньше человек,
    # пожертвовавший дважды, показывался на странице сбора как «2 человека
    # уже помогли». Проверка идёт под той же блокировкой строки сбора,
    # что и всё остальное, поэтому гонки здесь нет.
    is_new_donor = not Donation.objects.filter(
        fundraise=fundraise, donor=donor, refunded_at__isnull=True,
    ).exists()

    fundraise.current_amount += amount
    if is_new_donor:
        fundraise.donors_count += 1
    fundraise.save(update_fields=['current_amount', 'donors_count'])

    donation = Donation.objects.create(
        donor=donor,
        fundraise=fundraise,
        amount=amount,
        message=message,
        is_anonymous=is_anonymous,
    )

    if commission > 0:
        CommissionTransaction.objects.create(
            donation=donation,
            amount=commission,
            percent=Decimal(settings.DONATION_COMMISSION_PERCENT),
        )

    comment = f'Пожертвование на сбор «{fundraise.title}»'
    if is_anonymous:
        comment += ' (анонимно)'
    elif message:
        comment += f' — «{message[:50]}»'
    Transaction.objects.create(
        sender=donor,
        receiver=author,
        amount=author_amount,
        comment=comment,
        status='completed',
        is_donation=True,
        kind='donation',
        fundraise_id=fundraise.pk,
    )

    return donation


@db_transaction.atomic
def refund_donations(fundraise, reason='сбор отменён автором'):
    """
    Возврат пожертвований донорам при отмене сбора.

    Раньше отмена сбора просто меняла статус, а деньги оставались у автора:
    можно было собрать средства «на лечение», отменить сбор и вывести их.

    Если автор успел потратить часть средств, возвращается столько, сколько
    есть на его балансе, а остаток фиксируется как долг в комментарии
    транзакции — деньги нельзя создать из ничего, и молчать об этом нельзя.
    """
    fundraise = Fundraise.objects.select_for_update().get(pk=fundraise.pk)
    donations = list(
        Donation.objects.filter(fundraise=fundraise, refunded_at__isnull=True)
        .select_related('donor')
        .order_by('created_at')
    )
    if not donations:
        return {'refunded': Decimal('0.00'), 'shortfall': Decimal('0.00'), 'count': 0}

    author = fundraise.author
    # Комиссия возвращается донору со служебного аккаунта: она была удержана
    # с пожертвования, значит при отмене сбора обязана вернуться тоже
    has_commission = any(getattr(d, 'commission', None) for d in donations)
    service_account = get_service_account() if has_commission else None

    participants = [author] + [donation.donor for donation in donations]
    if service_account:
        participants.append(service_account)
    balances = _lock_balances(*participants)
    author_balance = balances[author.pk]

    refunded_total = Decimal('0.00')
    shortfall_total = Decimal('0.00')
    refunded_count = 0

    from django.utils import timezone

    for donation in donations:
        commission = getattr(donation, 'commission', None)
        commission_amount = commission.amount if commission else Decimal('0.00')
        donor_balance = balances[donation.donor_id]

        # Уже возвращённое вычитается: повторный вызов после погашения долга
        # автором обязан довозвращать остаток, а не выплачивать сумму заново.
        # Раньше этого не было, и второй проход отдавал донору больше, чем он
        # пожертвовал, — за счёт автора.
        already_returned = donation.refunded_amount or Decimal('0.00')
        outstanding = donation.amount - already_returned
        if outstanding <= 0:
            continue

        returned_now = Decimal('0.00')

        # Первой возвращается комиссия — это деньги сервиса, а не автора,
        # и жертвователь платил её за услугу, которая не была оказана.
        # Порядок фиксирован, иначе при частичном возврате непонятно,
        # чья часть уже отдана.
        commission_due = max(Decimal('0.00'), commission_amount - already_returned)
        if commission_due > 0 and service_account:
            service_balance = balances[service_account.pk]
            returned_commission = min(commission_due, service_balance.amount)
            if returned_commission > 0:
                service_balance.amount -= returned_commission
                service_balance.save(update_fields=['amount'])
                donor_balance.amount += returned_commission
                donor_balance.save(update_fields=['amount'])
                returned_now += returned_commission
            if returned_commission < commission_due:
                # Если на служебном счёте не хватило, это долг сервиса,
                # а не автора: автор получил сумму за вычетом комиссии
                # и больше отдавать не должен.
                logger.error(
                    'Комиссия %s ₽ по пожертвованию #%s не возвращена: '
                    'на служебном счёте недостаточно средств',
                    commission_due - returned_commission, donation.pk,
                )

        # Автор возвращает только то, что получил сам: сумму пожертвования
        # за вычетом комиссии, минус уже возвращённое им ранее.
        already_from_author = max(Decimal('0.00'), already_returned - commission_amount)
        author_due = max(
            Decimal('0.00'),
            (donation.amount - commission_amount) - already_from_author,
        )
        from_author = min(author_due, author_balance.amount)
        if from_author > 0:
            author_balance.amount -= from_author
            author_balance.save(update_fields=['amount'])
            donor_balance.amount += from_author
            donor_balance.save(update_fields=['amount'])
            returned_now += from_author

        refunded_total += returned_now
        if returned_now > 0:
            refunded_count += 1

        total_returned = already_returned + returned_now
        missing = donation.amount - total_returned
        if missing > 0:
            shortfall_total += missing

        comment = f'Возврат пожертвования #{donation.pk} ({reason})'
        if missing > 0:
            comment += f'; не возвращено {missing} ₽ — недостаточно средств у автора сбора'

        # Транзакция создаётся только на реально перемещённые деньги.
        # Раньше при нулевом возврате писалась фиктивная запись на 0.01 ₽ —
        # несуществующий платёж в истории и в выгрузке данных.
        if returned_now > 0:
            Transaction.objects.create(
                sender=author,
                receiver=donation.donor,
                amount=returned_now,
                comment=comment,
                status='completed' if missing == 0 else 'failed',
                kind='refund',
                fundraise_id=fundraise.pk,
            )
        else:
            logger.error(
                'Возврат пожертвования #%s невозможен: %s', donation.pk, comment,
            )

        # refunded_at ставится только при полном возврате: иначе повторить
        # возврат после погашения долга автором будет нельзя —
        # выборка отбирает донаты по refunded_at__isnull=True
        donation.refunded_amount = total_returned
        if missing <= 0:
            donation.refunded_at = timezone.now()
        donation.save(update_fields=['refunded_at', 'refunded_amount'])

    if shortfall_total > 0:
        logger.error(
            'Сбор #%s отменён, но %s ₽ не удалось вернуть донорам: средств у автора %s не хватило',
            fundraise.pk, shortfall_total, author.username,
        )

    # Счётчики сбора приводятся в соответствие с тем, что у него осталось
    # фактически. Раньше возврат их не трогал, и отменённый сбор продолжал
    # показывать «собрано 1000 ₽, 1 жертвователь» — деньги вернулись, а
    # цифра осталась и попадала в общую статистику.
    #
    # Собранное уменьшается на реально возвращённое (в том числе при
    # частичном возврате), а счётчик жертвователей — только на тех, кому
    # вернули всё: человек, которому вернули половину, из сбора не ушёл.
    if refunded_total > 0:
        fundraise.current_amount = max(
            Decimal('0.00'), fundraise.current_amount - refunded_total,
        )
        # Пересчёт по людям: жертвователь уходит из счётчика, только когда
        # возвращены все его пожертвования в этот сбор
        fundraise.donors_count = (
            Donation.objects
            .filter(fundraise=fundraise, refunded_at__isnull=True)
            .values('donor').distinct().count()
        )
        fundraise.save(update_fields=['current_amount', 'donors_count'])

    return {
        'refunded': refunded_total,
        'shortfall': shortfall_total,
        'count': refunded_count,
    }


@db_transaction.atomic
def hold_for_withdrawal(user, amount, payment_method, payment_details, masked):
    """
    Создание заявки на вывод с удержанием суммы.

    Списание и создание заявки — одна транзакция: иначе при сбое между ними
    деньги исчезали бы с баланса, не превратившись в заявку.
    """
    from main.models import WithdrawalRequest

    amount = quantize(amount)
    ensure_can_operate(user)
    ensure_email_confirmed(user, 'Вывод средств')
    balances = _lock_balances(user)
    _debit(balances[user.pk], amount)

    return WithdrawalRequest.objects.create(
        user=user,
        amount=amount,
        payment_method=payment_method,
        payment_details=payment_details,
        payment_details_masked=masked,
    )


@db_transaction.atomic
def credit_payment(payment_transaction):
    """
    Зачисление подтверждённого платежа на баланс.

    Идемпотентно: запись платежа блокируется, и если она уже в статусе
    'paid', повторное уведомление ничего не зачисляет. Без этого
    дублирующийся вебхук (а ЮKassa повторяет доставку при таймауте)
    начислял бы сумму дважды.
    """
    from django.utils import timezone
    from main.models import PaymentTransaction

    locked = PaymentTransaction.objects.select_for_update().get(pk=payment_transaction.pk)
    if locked.status == 'paid':
        return False

    balance = Balance.objects.select_for_update().get(user=locked.user)
    balance.amount += locked.amount
    balance.save(update_fields=['amount'])

    locked.status = 'paid'
    locked.paid_at = timezone.now()
    locked.save(update_fields=['status', 'paid_at'])

    Transaction.objects.create(
        sender=locked.user,
        receiver=locked.user,
        amount=locked.amount,
        comment=f'Пополнение баланса ({locked.get_payment_method_display()})',
        status='completed',
        kind='topup',
    )
    return True


def outstanding_debt(user):
    """
    Долг автора перед жертвователями по отменённым и отклонённым сборам.

    Возникает, когда на момент отмены автор уже потратил собранное:
    возврат идёт в пределах остатка, а недостача фиксируется. Пункт 7.4
    оферты обещает, что такой долг погашается, — но до появления этой
    функции его нельзя было ни увидеть, ни погасить.

    Возвращает словарь: общая сумма долга и разбивка по сборам.
    """
    unpaid = (
        Donation.objects
        .filter(fundraise__author=user, fundraise__status='cancelled', refunded_at__isnull=True)
        .select_related('fundraise')
    )

    by_fundraise = {}
    total = Decimal('0.00')
    for donation in unpaid:
        remaining = donation.amount - (donation.refunded_amount or Decimal('0.00'))
        if remaining <= 0:
            continue
        total += remaining
        entry = by_fundraise.setdefault(donation.fundraise_id, {
            'fundraise': donation.fundraise,
            'amount': Decimal('0.00'),
            'donors': set(),
        })
        entry['amount'] += remaining
        # Людей, а не записей: один человек мог пожертвовать дважды,
        # и «жертвователей: 2» было бы неправдой
        entry['donors'].add(donation.donor_id)

    items = []
    for entry in by_fundraise.values():
        items.append({
            'fundraise': entry['fundraise'],
            'amount': entry['amount'],
            'donors': len(entry['donors']),
        })
    return {'total': total, 'items': items}


@db_transaction.atomic
def repay_debt(user, fundraise=None):
    """
    Погашение долга перед жертвователями за счёт баланса автора.

    Повторно вызывает возврат по тем же пожертвованиям: механизм умеет
    довозвращать остаток и не переплачивает, потому что учитывает уже
    возвращённое.

    ensure_can_operate здесь намеренно не вызывается — это единственная
    расходная операция без такой проверки. Причина: деньги уходят не
    произвольному получателю, а конкретным жертвователям, чьи пожертвования
    зафиксированы записями Donation, и ровно в размере невозвращённого.
    Пункт 9.4 оферты прямо говорит, что ограничение не влечёт утрату права
    на средства, а раздел 7.4 обязывает вернуть долг; запрет на возврат
    денег жертвователям превратил бы приостановление операций в способ
    удерживать чужие деньги.

    Возвращает сумму, которую удалось вернуть.
    """
    fundraises = Fundraise.objects.filter(
        author=user, status='cancelled',
        donations__refunded_at__isnull=True,
    ).distinct()
    if fundraise is not None:
        fundraises = fundraises.filter(pk=fundraise.pk)

    repaid = Decimal('0.00')
    for item in fundraises:
        result = refund_donations(item, reason='погашение задолженности автором')
        repaid += result['refunded']
    return repaid
