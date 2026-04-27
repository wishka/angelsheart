# main/payments/base.py
from decimal import Decimal
from abc import ABC, abstractmethod


class BasePaymentProvider(ABC):
    """Базовый абстрактный класс для всех платежных систем"""
    
    @abstractmethod
    def create_payment(self, amount: Decimal, user_id: int, metadata: dict = None) -> dict:
        """Создание платежа для пополнения баланса"""
        pass
    
    @abstractmethod
    def check_payment(self, payment_id: str) -> dict:
        """Проверка статуса платежа"""
        pass
    
    @abstractmethod
    def process_withdrawal(self, user_id: int, amount: Decimal, details: dict) -> dict:
        """Вывод средств (массовые выплаты)"""
        pass
    
    @abstractmethod
    def refund_payment(self, payment_id: str, amount: Decimal = None) -> dict:
        """Возврат платежа"""
        pass


class MockPaymentProvider(BasePaymentProvider):
    """Mock-провайдер для тестирования (симуляция оплаты)"""
    
    def create_payment(self, amount: Decimal, user_id: int, metadata: dict = None) -> dict:
        import uuid
        return {
            'success': True,
            'payment_id': str(uuid.uuid4()),
            'confirmation_url': '/payment/simulate/',
            'status': 'pending'
        }
    
    def check_payment(self, payment_id: str) -> dict:
        return {
            'success': True,
            'status': 'succeeded',
            'amount': Decimal('100'),
            'paid_at': None
        }
    
    def process_withdrawal(self, user_id: int, amount: Decimal, details: dict) -> dict:
        import uuid
        return {
            'success': True,
            'payout_id': str(uuid.uuid4()),
            'status': 'succeeded'
        }
    
    def refund_payment(self, payment_id: str, amount: Decimal = None) -> dict:
        import uuid
        return {
            'success': True,
            'refund_id': str(uuid.uuid4()),
            'status': 'succeeded'
        }