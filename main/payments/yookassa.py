import base64
import logging
import uuid
from decimal import Decimal
from io import BytesIO

import qrcode
from django.conf import settings

from .base import BasePaymentProvider

logger = logging.getLogger('withdrawals')

try:
    from yookassa import Configuration, Payment, Payout, Refund

    YOOKASSA_AVAILABLE = True
except ImportError:
    YOOKASSA_AVAILABLE = False
    # print на импорте писал в stdout при каждом запуске любой команды
    logger.warning('Пакет yookassa не установлен: pip install yookassa')


class YooKassaProvider(BasePaymentProvider):
    """Интеграция с платежной системой ЮKassa"""
    
    def __init__(self):
        if not YOOKASSA_AVAILABLE:
            raise ImportError("yookassa package is required. Install with: pip install yookassa")
        
        # Настройка ЮKassa
        Configuration.account_id = getattr(settings, 'YOOKASSA_SHOP_ID', '')
        Configuration.secret_key = getattr(settings, 'YOOKASSA_SECRET_KEY', '')
        
        self.mode = getattr(settings, 'PAYMENT_MODE', 'test')
        self.success_url = getattr(settings, 'PAYMENT_SUCCESS_URL', 'http://localhost:8000/payment/success/')
        self.cancel_url = getattr(settings, 'PAYMENT_CANCEL_URL', 'http://localhost:8000/payment/cancel/')
    
    def create_payment(self, amount: Decimal, user_id: int, metadata: dict = None) -> dict:
        """
        Создание платежа для пополнения баланса
        """
        payment_id = str(uuid.uuid4())
        
        try:
            # Создаем платеж в ЮKassa
            payment = Payment.create({
                "amount": {
                    "value": str(float(amount)),
                    "currency": "RUB"
                },
                "payment_method_data": {
                    "type": metadata.get('payment_method', 'bank_card') if metadata else 'bank_card'
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.success_url
                },
                "metadata": {
                    "user_id": user_id,
                    "payment_id": payment_id,
                    "type": "top_up",
                    **(metadata or {})
                },
                "capture": True,
                "description": f"Пополнение баланса пользователя #{user_id} на {amount} ₽"
            })
            
            return {
                'success': True,
                'payment_id': payment.id,
                'confirmation_url': payment.confirmation.confirmation_url,
                'status': payment.status
            }
        
        except Exception as e:
            print(f"YooKassa create_payment error: {str(e)}")
            return {
                'success': False,
                'error': f"Ошибка создания платежа: {str(e)}"
            }
    
    def check_payment(self, payment_id: str) -> dict:
        """Проверка статуса платежа"""
        try:
            payment = Payment.find_one(payment_id)
            
            return {
                'success': True,
                'status': payment.status,
                'amount': Decimal(payment.amount.value),
                'paid_at': payment.paid_at,
                'metadata': payment.metadata if hasattr(payment, 'metadata') else {}
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def process_withdrawal(self, user_id: int, amount: Decimal, details: dict) -> dict:
        """Вывод средств (массовые выплаты)"""
        try:
            from yookassa import Payout
            
            # Создание выплаты на карту
            payout = Payout.create({
                "amount": {
                    "value": str(float(amount)),
                    "currency": "RUB"
                },
                "payout_destination_data": {
                    "type": "bank_card",
                    "card": {
                        "number": details.get('card_number')
                    }
                },
                "metadata": {
                    "user_id": user_id,
                    "type": "withdrawal"
                },
                "description": f"Вывод средств пользователю #{user_id} на сумму {amount} ₽"
            })
            
            return {
                'success': True,
                'payout_id': payout.id,
                'status': payout.status
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def generate_qr_code_base64(self, data: str) -> str:
        """Генерирует QR-код в формате base64"""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    def create_sbp_qr_payment(self, amount: Decimal, order_id: str, description: str) -> dict:
        """
        Создает платеж и генерирует QR-код для оплаты через СБП.
        """
        try:
            # 1. Создаем стандартный платеж, но указываем метод оплаты "sbp"
            payment = Payment.create({
                "amount": {
                    "value": str(float(amount)),
                    "currency": "RUB"
                },
                "payment_method_data": {
                    "type": "sbp"  # Указываем СБП
                },
                "confirmation": {
                    "type": "qr",  # Говорим API, что хотим получить QR-код
                    "return_url": self.success_url
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "order_id": order_id,
                }
            })
            
            # 2. Извлекаем из ответа данные для QR-кода
            qr_payload = payment.confirmation.confirmation_data
            
            # 3. Генерируем QR-код
            qr_image = self.generate_qr_code_base64(qr_payload)
            
            return {
                'success': True,
                'payment_id': payment.id,
                'qr_code': qr_image,
                'status': payment.status
            }
        except Exception as e:
            print(f"Ошибка создания СБП платежа: {e}")
            return {'success': False, 'error': str(e)}
    
    def refund_payment(self, payment_id: str, amount: Decimal = None) -> dict:
        """Возврат платежа"""
        try:
            refund_data = {
                "payment_id": payment_id
            }
            
            if amount:
                refund_data["amount"] = {
                    "value": str(float(amount)),
                    "currency": "RUB"
                }
            
            refund = Refund.create(refund_data)
            
            return {
                'success': True,
                'refund_id': refund.id,
                'status': refund.status
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


# MockPaymentProvider здесь был дубликатом класса из base.py, а псевдонимы
# SBPProvider = YooKassaProvider и StripeProvider = MockPaymentProvider
# создавали видимость поддержки трёх платёжных систем: выплата «через Stripe»
# на деле уходила в заглушку и рапортовала об успехе, не переводя денег.
# Единственный реальный провайдер — YooKassaProvider выше;
# заглушка для тестов живёт в base.MockPaymentProvider.