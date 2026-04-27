import pyotp
import qrcode
from io import BytesIO
import base64
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from decimal import Decimal


class TwoFactorAuthService:
    """Сервис для двухфакторной аутентификации"""
    
    @staticmethod
    def generate_secret():
        """Генерация секретного ключа"""
        return pyotp.random_base32()
    
    @staticmethod
    def generate_qr_code(secret, user_email):
        """Генерация QR-кода для Google Authenticator"""
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=user_email, issuer_name="Ангел-Хранитель")
        
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    @staticmethod
    def verify_code(secret, code):
        """Проверка кода аутентификации"""
        totp = pyotp.TOTP(secret)
        return totp.verify(code)
    
    @staticmethod
    def generate_backup_codes():
        """Генерация резервных кодов"""
        import secrets
        return [secrets.token_hex(4) for _ in range(10)]


class AntiFraudService:
    """Сервис для обнаружения мошеннических операций"""
    
    def __init__(self, user):
        self.user = user
    
    def check_withdrawal(self, amount: Decimal, ip_address: str) -> tuple:
        """Проверка запроса на вывод"""
        risks = []
        
        # 1. Сумма вывода
        if amount > Decimal('50000'):
            risks.append("Крупная сумма вывода")
        
        # 2. Частота выводов
        from main.models import WithdrawalRequest
        withdrawals_today = WithdrawalRequest.objects.filter(
            user=self.user,
            created_at__date=timezone.now().date()
        ).count()
        
        if withdrawals_today >= 5:
            risks.append("Слишком много выводов за день")
        
        # 3. Новая карта/кошелек
        # Можно проверить, используется ли реквизит впервые
        
        # 4. IP-адрес
        # Можно проверить, не входит ли IP в черный список
        
        risk_level = 'low'
        if len(risks) >= 2:
            risk_level = 'high'
        elif len(risks) >= 1:
            risk_level = 'medium'
        
        return risk_level, risks
    
    def log_security_event(self, action, request, details=None):
        """Логирование событий безопасности"""
        from main.models import SecurityLog
        
        SecurityLog.objects.create(
            user=self.user,
            action=action,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            details=details or {}
        )