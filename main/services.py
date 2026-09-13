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

    fundraise.current_amount += amount
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

        # Остальное возвращает автор — в пределах того, что у него есть
        author_due = outstanding - commission_due
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
        fully_returned = sum(1 for d in donations if d.refunded_at is not None)
        fundraise.current_amount = max(
            Decimal('0.00'), fundraise.current_amount - refunded_total,
        )
        fundraise.donors_count = max(0, fundraise.donors_count - fully_returned)
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
