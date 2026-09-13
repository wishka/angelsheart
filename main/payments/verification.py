"""
Проверка данных верификации и лимитов по её уровню.

Лимиты больше не дублируются здесь: единственный источник —
settings.VERIFICATION_LIMITS. Раньше те же цифры были записаны в трёх
файлах, а функция проверки дневного лимита падала с NameError, потому
что `models` не был импортирован.
"""

import re
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

PASSPORT_SERIES_RE = re.compile(r'^\d{4}$')
PASSPORT_NUMBER_RE = re.compile(r'^\d{6}$')
NAME_PART_RE = re.compile(r'^[А-Яа-яЁё][А-Яа-яЁё-]*$')


class KYCService:
    """Сервис для верификации пользователей"""

    @staticmethod
    def validate_passport(data):
        """Проверка паспортных данных"""
        errors = {}

        series = (data.get('passport_series') or '').strip()
        if not PASSPORT_SERIES_RE.match(series):
            errors['passport_series'] = 'Серия паспорта должна содержать 4 цифры'

        number = (data.get('passport_number') or '').strip()
        if not PASSPORT_NUMBER_RE.match(number):
            errors['passport_number'] = 'Номер паспорта должен содержать 6 цифр'

        return errors

    @staticmethod
    def validate_name(full_name):
        """Проверка ФИО"""
        parts = (full_name or '').strip().split()
        if len(parts) < 2:
            return 'Укажите фамилию и имя'
        if len(parts) > 4:
            return 'Проверьте написание ФИО'

        for part in parts:
            if not NAME_PART_RE.match(part):
                return 'Используйте только русские буквы'

        return None

    @staticmethod
    def get_verification_limits(verification_level):
        """Лимиты для уровня верификации (из настроек проекта)."""
        return settings.VERIFICATION_LIMITS.get(
            verification_level, settings.VERIFICATION_LIMITS['unverified']
        )

    @staticmethod
    def get_level(user):
        verification = getattr(user, 'verification', None)
        return verification.level if verification else 'unverified'

    @staticmethod
    def check_withdrawal_limit(user, amount):
        """
        Проверка лимитов вывода по уровню верификации.

        Возвращает (is_allowed, error_message).
        """
        from main.models import WithdrawalRequest

        amount = Decimal(amount)
        limits = KYCService.get_verification_limits(KYCService.get_level(user))

        if amount > limits['single_withdrawal']:
            return False, (
                f'Максимальная сумма одной выплаты для вашего уровня верификации — '
                f'{limits["single_withdrawal"]} ₽. Повысьте уровень верификации.'
            )

        counted = ['pending', 'processing', 'completed']
        today = timezone.now().date()

        daily_total = WithdrawalRequest.objects.filter(
            user=user, created_at__date=today, status__in=counted,
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

        if daily_total + amount > limits['daily_withdrawal']:
            return False, (
                f'Дневной лимит вывода для вашего уровня верификации — '
                f'{limits["daily_withdrawal"]} ₽ (сегодня уже {daily_total} ₽)'
            )

        monthly_limit = limits['monthly_withdrawal']
        if monthly_limit is not None:
            month_start = today.replace(day=1)
            monthly_total = WithdrawalRequest.objects.filter(
                user=user, created_at__date__gte=month_start, status__in=counted,
            ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

            if monthly_total + amount > monthly_limit:
                return False, (
                    f'Месячный лимит вывода для вашего уровня верификации — '
                    f'{monthly_limit} ₽ (в этом месяце уже {monthly_total} ₽)'
                )

        return True, None

    @staticmethod
    def check_payment_limit(user, amount):
        """
        Проверка дневного лимита пополнения.

        Лимиты на приём средств были объявлены в настройках, но нигде не
        применялись: неверифицированный пользователь мог пополнять баланс
        без ограничений, что противоречит и заявленным лимитам, и
        ограничениям ст. 10 161-ФЗ для неперсонифицированных средств платежа.
        """
        from main.models import PaymentTransaction

        amount = Decimal(amount)
        limits = KYCService.get_verification_limits(KYCService.get_level(user))
        today = timezone.now().date()

        daily_total = PaymentTransaction.objects.filter(
            user=user, created_at__date=today, status__in=['pending', 'paid'],
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')

        if daily_total + amount > limits['daily_payment']:
            return False, (
                f'Дневной лимит пополнения для вашего уровня верификации — '
                f'{limits["daily_payment"]} ₽ (сегодня уже {daily_total} ₽)'
            )

        return True, None
