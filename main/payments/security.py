import hashlib
import hmac
import secrets
from decimal import Decimal
from io import BytesIO
import base64

import pyotp
import qrcode
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


class LoginRateLimiter:
    """
    Ограничение числа неудачных попыток входа.

    Раньше формы входа не были защищены вовсе: 12 неверных паролей подряд
    проходили без единой задержки, а throttling DRF на веб-формы не
    распространяется.

    Счётчик ведётся отдельно по IP и по имени пользователя: первый гасит
    перебор паролей к одному аккаунту, второй — попытки с ботнета.

    Хранилище — кеш, поэтому в проде обязателен Redis: LocMemCache живёт
    внутри одного воркера, и перебор обошёл бы лимит, попав в соседний.
    """

    def __init__(self, ip_address):
        self.ip_address = ip_address or 'unknown'

    def _keys(self, username):
        keys = [f'login-fail:ip:{self.ip_address}']
        if username:
            digest = hashlib.sha256(username.lower().encode()).hexdigest()[:32]
            keys.append(f'login-fail:user:{digest}')
        return keys

    def blocked_for(self, username=None):
        """Сколько секунд осталось до разблокировки (0 — не заблокирован)."""
        for key in self._keys(username):
            if cache.get(f'{key}:locked'):
                return cache.ttl(f'{key}:locked') if hasattr(cache, 'ttl') else settings.LOGIN_LOCKOUT_SECONDS
        return 0

    def register_failure(self, username=None):
        """Учесть неудачную попытку и при превышении порога выставить блокировку."""
        for key in self._keys(username):
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.set(key, 1, settings.LOGIN_ATTEMPT_WINDOW)
                attempts = 1
            if attempts >= settings.LOGIN_MAX_ATTEMPTS:
                cache.set(f'{key}:locked', True, settings.LOGIN_LOCKOUT_SECONDS)
                cache.delete(key)

    def reset(self, username=None):
        """Сбросить счётчики после успешного входа."""
        for key in self._keys(username):
            cache.delete(key)
            cache.delete(f'{key}:locked')


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
        """
        Проверка одноразового кода.

        valid_window=1 допускает соседний 30-секундный интервал: без него
        код отвергается при расхождении часов телефона и сервера.
        """
        if not secret or not code:
            return False
        code = str(code).strip().replace(' ', '')
        if not code.isdigit():
            return False
        return pyotp.TOTP(secret).verify(code, valid_window=1)

    @staticmethod
    def generate_backup_codes():
        """Генерация резервных кодов (показываются пользователю один раз)."""
        return [secrets.token_hex(4) for _ in range(10)]

    @staticmethod
    def consume_backup_code(two_factor, code):
        """
        Проверка и погашение резервного кода.

        Сравнение через compare_digest, чтобы время ответа не зависело от
        количества совпавших символов. Использованный код удаляется.
        """
        if not code:
            return False
        code = str(code).strip().lower().replace(' ', '')
        remaining = list(two_factor.backup_codes or [])

        for stored in remaining:
            if hmac.compare_digest(str(stored).lower(), code):
                remaining.remove(stored)
                two_factor.backup_codes = remaining
                two_factor.last_used = timezone.now()
                two_factor.save(update_fields=['backup_codes', 'last_used'])
                return True
        return False


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
    
    def log_security_event(self, action, request, details=None, username_attempted=''):
        """
        Запись события безопасности.

        IP берётся через get_client_ip: прямое чтение REMOTE_ADDR давало
        неверный адрес за прокси, а доверие к X-Forwarded-For позволяло
        клиенту записать в журнал любой чужой адрес.
        """
        from main.models import SecurityLog
        from main.utils.request_meta import get_request_meta

        ip_address, user_agent = get_request_meta(request)

        return SecurityLog.objects.create(
            user=self.user if getattr(self.user, 'pk', None) else None,
            action=action,
            username_attempted=(username_attempted or '')[:150],
            ip_address=ip_address,
            user_agent=user_agent,
            details=details or {},
        )