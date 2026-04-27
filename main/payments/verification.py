from django.core.validators import ValidationError
import re


class KYCService:
    """Сервис для верификации пользователей"""
    
    @staticmethod
    def validate_passport(data):
        """Проверка паспортных данных"""
        errors = {}
        
        # Паспортная серия (4 цифры)
        series = data.get('passport_series', '')
        if not re.match(r'^\d{4}$', series):
            errors['passport_series'] = 'Серия паспорта должна содержать 4 цифры'
        
        # Паспортный номер (6 цифр)
        number = data.get('passport_number', '')
        if not re.match(r'^\d{6}$', number):
            errors['passport_number'] = 'Номер паспорта должен содержать 6 цифр'
        
        return errors
    
    @staticmethod
    def validate_name(full_name):
        """Проверка ФИО"""
        parts = full_name.strip().split()
        if len(parts) < 2:
            return "Укажите фамилию и имя"
        
        for part in parts:
            if not re.match(r'^[А-Яа-яЁё-]+$', part):
                return "Используйте только русские буквы"
        
        return None
    
    @staticmethod
    def get_verification_limits(verification_level):
        """Получение лимитов в зависимости от уровня верификации"""
        limits = {
            'unverified': {
                'daily_withdrawal': 1000,
                'monthly_withdrawal': 5000,
                'single_withdrawal': 500,
                'daily_payment': 10000,
            },
            'basic': {
                'daily_withdrawal': 10000,
                'monthly_withdrawal': 50000,
                'single_withdrawal': 5000,
                'daily_payment': 50000,
            },
            'full': {
                'daily_withdrawal': 100000,
                'monthly_withdrawal': None,  # Без ограничений
                'single_withdrawal': 50000,
                'daily_payment': 500000,
            },
        }
        return limits.get(verification_level, limits['unverified'])
    
    @staticmethod
    def check_withdrawal_limit(user, amount):
        """Проверка лимитов вывода по верификации"""
        from main.models import UserVerification
        
        verification = getattr(user, 'verification', None)
        if not verification:
            level = 'unverified'
        else:
            level = verification.level
        
        limits = KYCService.get_verification_limits(level)
        
        # Проверка одноразового лимита
        if amount > limits['single_withdrawal']:
            return False, f"Максимальная сумма одной выплаты: {limits['single_withdrawal']} ₽"
        
        # Проверка дневного лимита
        from main.models import WithdrawalRequest
        from django.utils import timezone
        
        daily_total = WithdrawalRequest.objects.filter(
            user=user,
            created_at__date=timezone.now().date(),
            status__in=['pending', 'processing', 'completed']
        ).aggregate(sum=models.Sum('amount'))['sum'] or 0
        
        if daily_total + amount > limits['daily_withdrawal']:
            return False, f"Дневной лимит вывода: {limits['daily_withdrawal']} ₽"
        
        return True, None