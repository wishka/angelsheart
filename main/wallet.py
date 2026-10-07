"""
Операции кошелька, общие для сайта и мобильного API: пополнение, вывод,
верификация личности.

Раньше эти правила жили прямо во вью сайта. Мобильному API нужны те же
самые проверки — лимиты по уровню верификации (115-ФЗ, ст. 10 161-ФЗ),
антифрод, возраст 18+, — и копия во второй вью разошлась бы с первой при
первой же правке. Теперь вью сайта и API вызывают эти функции, а сами
только показывают результат: сообщением на странице или JSON.

Все отказы — OperationRejected с текстом для человека.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from main import services
from main.models import Balance, KYCDocument, PaymentTransaction, Transaction, UserVerification
from main.payments.security import AntiFraudService
from main.payments.verification import KYCService
from main.services import InsufficientFunds, OperationRejected
from main.utils.encryption import mask_card_number, mask_phone
from main.utils.request_meta import get_client_ip

logger = logging.getLogger('withdrawals')


# ==================== ПОПОЛНЕНИЕ ====================

def check_topup_amount(amount):
    if amount < settings.MIN_TOPUP_AMOUNT:
        raise OperationRejected(f'Минимальная сумма пополнения — {settings.MIN_TOPUP_AMOUNT} ₽')
    if amount > settings.MAX_TOPUP_AMOUNT:
        raise OperationRejected(f'Максимальная сумма пополнения — {settings.MAX_TOPUP_AMOUNT} ₽')


def simulated_topup(user, amount):
    """
    Пополнение без платёжной системы — только для разработки
    (ALLOW_SIMULATED_TOPUP) и с потолком: без него два запроса подряд
    давали бы сколько угодно денег из ничего.
    """
    check_topup_amount(amount)
    simulated_total = Transaction.objects.filter(
        sender=user, kind='topup', status='completed',
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
    if simulated_total + amount > settings.SIMULATED_TOPUP_TOTAL_LIMIT:
        raise OperationRejected(
            f'В тестовом режиме суммарное пополнение ограничено '
            f'{settings.SIMULATED_TOPUP_TOTAL_LIMIT} ₽ (уже пополнено {simulated_total} ₽)'
        )
    with db_transaction.atomic():
        balance = Balance.objects.select_for_update().get(user=user)
        balance.amount += amount
        balance.save(update_fields=['amount'])
        Transaction.objects.create(
            sender=user, receiver=user, amount=amount,
            comment='Пополнение баланса (тестовый режим)', status='completed', kind='topup',
        )


def create_topup_payment(user, amount, payment_method, request):
    """
    Платёж ЮKassa. Возвращает адрес страницы оплаты: сайт перенаправляет
    туда сразу, приложение открывает её в браузере. Зачисление — по
    вебхуку ЮKassa, как и раньше; ни сайт, ни приложение баланс сами
    не меняют.
    """
    from main.payments.yookassa import YooKassaProvider

    check_topup_amount(amount)
    allowed_methods = {choice[0] for choice in PaymentTransaction.METHOD_CHOICES}
    if payment_method not in allowed_methods:
        raise OperationRejected('Выбранный способ оплаты не поддерживается')

    allowed, limit_error = KYCService.check_payment_limit(user, amount)
    if not allowed:
        raise OperationRejected(limit_error)

    try:
        result = YooKassaProvider().create_payment(
            amount=amount, user_id=user.id, metadata={'payment_method': payment_method},
        )
    except Exception:
        logger.exception('Ошибка обращения к платёжной системе')
        raise OperationRejected('Платёжная система временно недоступна, попробуйте позже')

    if not result.get('success'):
        logger.warning('ЮKassa отказала в создании платежа: %s', result.get('error'))
        raise OperationRejected('Не удалось создать платёж. Попробуйте ещё раз.')

    PaymentTransaction.objects.create(
        user=user, amount=amount, payment_method=payment_method,
        payment_id=result['payment_id'], status='pending',
        metadata={'confirmation_url': result['confirmation_url']},
    )
    AntiFraudService(user).log_security_event(
        'payment', request, details={'amount': str(amount), 'payment_method': payment_method},
    )
    return result['confirmation_url']


# ==================== ВЫВОД ====================

def create_withdrawal(user, data, request):
    """
    Заявка на вывод из данных формы (сайт) или JSON (приложение).
    Проверки те же, что раньше были во вью сайта: форма, валидатор
    лимитов по уровню верификации, антифрод, удержание средств.
    """
    from main.forms import WithdrawalForm
    from main.payments.withdrawals import WithdrawalValidator

    form = WithdrawalForm(data)
    if not form.is_valid():
        errors = [message for messages in form.errors.values() for message in messages]
        raise OperationRejected('\n'.join(errors) or 'Проверьте данные заявки')

    amount = services.quantize(form.cleaned_data['amount'])
    payment_method = form.cleaned_data['payment_method']
    if payment_method == 'card':
        payment_details = {
            'card_number': form.cleaned_data.get('card_number'),
            'card_holder': form.cleaned_data.get('card_holder'),
            'expiry_date': form.cleaned_data.get('expiry_date'),
        }
        masked = mask_card_number(payment_details['card_number'])
    elif payment_method == 'sbp':
        payment_details = {
            'phone_number': form.cleaned_data.get('phone_number'),
            'bank_id': form.cleaned_data.get('bank_id'),
        }
        masked = mask_phone(payment_details['phone_number'])
    else:
        payment_details = {'wallet_number': form.cleaned_data.get('wallet_number')}
        wallet = str(payment_details['wallet_number'] or '')
        masked = '***' + wallet[-4:] if len(wallet) >= 4 else '***'

    is_valid, error = WithdrawalValidator.validate_withdrawal_request(
        user=user, amount=amount, payment_details=payment_details, payment_method=payment_method,
    )
    if not is_valid:
        raise OperationRejected(error)

    risk_level, risks = AntiFraudService(user).check_withdrawal(amount, get_client_ip(request))
    try:
        withdrawal = services.hold_for_withdrawal(
            user=user, amount=amount, payment_method=payment_method,
            payment_details=payment_details, masked=masked,
        )
    except (InsufficientFunds, OperationRejected):
        raise
    except Exception:
        logger.exception('Ошибка создания заявки на вывод')
        raise OperationRejected('Не удалось создать заявку, попробуйте позже')

    logger.info('Создана заявка #%s на вывод %s ₽ от %s (риск: %s)',
                withdrawal.id, amount, user.username, risk_level)
    AntiFraudService(user).log_security_event(
        'withdrawal', request,
        details={'withdrawal_id': withdrawal.id, 'amount': str(amount),
                 'risk_level': risk_level, 'risks': risks},
    )
    return withdrawal


def withdrawal_limits(user):
    level = KYCService.get_level(user)
    limits = KYCService.get_verification_limits(level)
    return {
        'min_amount': settings.MIN_WITHDRAWAL_AMOUNT,
        'max_amount': min(settings.MAX_WITHDRAWAL_AMOUNT, limits['single_withdrawal']),
        'limits': limits,
        'verification_level': level,
    }


# ==================== ВЕРИФИКАЦИЯ ====================

def submit_verification(user, full_name, birth_date, passport_series, passport_number, request):
    """
    Паспортные данные — на проверку администратором. Уровень верификации
    сам по себе НЕ повышается: его повышает только администратор после
    проверки документов (раньше уровень давался за любые цифры).
    """
    from main.views import parse_birth_date, years_since

    name_error = KYCService.validate_name(full_name)
    if name_error:
        raise OperationRejected(name_error)
    passport_errors = KYCService.validate_passport({
        'passport_series': passport_series, 'passport_number': passport_number,
    })
    if passport_errors:
        raise OperationRejected('\n'.join(passport_errors.values()))

    parsed = parse_birth_date(birth_date)
    if parsed is None:
        raise OperationRejected('Укажите дату рождения в формате ДД.ММ.ГГГГ')
    age = years_since(parsed)
    if age < 18:
        raise OperationRejected('Сервис доступен только совершеннолетним: по указанной дате '
                                'рождения вам меньше 18 лет.')
    if age > 120:
        raise OperationRejected('Проверьте дату рождения — она указана неверно.')

    verification, _ = UserVerification.objects.get_or_create(user=user)
    verification.full_name = full_name
    verification.birth_date = parsed
    verification.passport_series = passport_series
    verification.passport_number = passport_number
    verification.submitted_at = timezone.now()
    verification.save(update_fields=['full_name', 'birth_date', 'passport_series',
                                     'passport_number', 'submitted_at'])
    AntiFraudService(user).log_security_event('verification', request,
                                              details={'stage': 'data_submitted'})
    return verification


def upload_kyc_document(user, document_type, upload, document_number, request):
    from main.views import validate_uploaded_document

    if document_type not in {choice[0] for choice in KYCDocument.DOCUMENT_TYPES}:
        raise OperationRejected('Выберите тип документа')
    if not upload:
        raise OperationRejected('Выберите файл')
    error = validate_uploaded_document(upload)
    if error:
        raise OperationRejected(error)
    document = KYCDocument.objects.create(
        user=user, document_type=document_type, document_number=document_number or '',
        document_image=upload, status='pending',
    )
    AntiFraudService(user).log_security_event(
        'verification', request, details={'stage': 'document_uploaded', 'type': document_type},
    )
    return document
