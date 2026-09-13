"""
Тесты на дефекты, найденные аудитом.

Каждый класс закрывает конкретную находку: тест падал бы на коде до
исправления. Файл заменяет прежний tests.py, в котором было три пустых
строки — именно поэтому все 14 дефектов дожили до рабочей ветки.
"""

import hashlib
import json
import re
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from main import email_confirmation, moderation, services
from main import restrictions as restrictions_service
from main.models import (
    AccountRestriction, add_business_days, DataBreachIncident, EmailConfirmation,
    Balance, CommissionTransaction, Donation, Fundraise, FundraiseDocument,
    KYCDocument, PaymentTransaction, PersonalDataAccessLog, SecurityLog,
    Transaction, TwoFactorAuth, UserConsent, UserVerification, WithdrawalRequest,
)
from main.payments.security import TwoFactorAuthService

PASSWORD = 'Sunrise-Harbor-42'
TRUSTED_IP = '185.71.76.1'  # из диапазона ЮKassa


def make_user(username, balance='0', email_confirmed=True):
    """
    Обычный пользователь сервиса.

    Адрес по умолчанию подтверждён: это состояние человека, который прошёл
    регистрацию до конца. Для проверок самого подтверждения есть
    email_confirmed=False.
    """
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password=PASSWORD,
    )
    Balance.objects.filter(user=user).update(amount=Decimal(balance))
    confirmation = EmailConfirmation.for_user(user)
    if email_confirmed:
        confirmation.confirm()
    return user


def make_service_account():
    """
    Служебный аккаунт для зачисления комиссии.

    В рабочем окружении создаётся командой create_service_account и обязан
    существовать всегда: без него операция с ненулевой комиссией не пройдёт.
    """
    from django.conf import settings

    user, _ = User.objects.get_or_create(
        username=settings.SERVICE_ACCOUNT_USERNAME,
        defaults={'is_active': False},
    )
    Balance.objects.get_or_create(user=user)
    return user


def total_money():
    """Сумма всех балансов: внутренние операции обязаны её сохранять."""
    from django.db.models import Sum

    return Balance.objects.aggregate(total=Sum('amount'))['total'] or Decimal('0')


class BaseCase(TestCase):
    def setUp(self):
        cache.clear()  # счётчики попыток входа живут в кеше
        # Служебный аккаунт есть в любом рабочем окружении
        self.service_account = make_service_account()


# ==================== ВЕБХУКИ ====================

class WebhookSecurityTests(BaseCase):
    """
    Раньше /payment/webhook/ зачисляла деньги по любому POST-запросу.
    Воспроизведено в аудите: +50 000 ₽ обычным curl без авторизации.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('webhook_user')
        self.payment = PaymentTransaction.objects.create(
            user=self.user, amount=Decimal('50000'), payment_method='card',
            payment_id='pay-1', status='pending',
        )
        self.body = json.dumps({'event': 'payment.succeeded', 'object': {'id': 'pay-1'}})

    def post(self, **extra):
        return self.client.post(
            reverse('main:payment_webhook'), data=self.body,
            content_type='application/json', **extra,
        )

    def test_forged_webhook_from_untrusted_ip_credits_nothing(self):
        response = self.post(REMOTE_ADDR='203.0.113.7')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('0'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    @patch('main.payments.yookassa.YooKassaProvider')
    def test_trusted_ip_but_api_says_not_paid_credits_nothing(self, provider):
        # Даже с подделанного «правильного» IP решает ответ API ЮKassa
        provider.return_value.check_payment.return_value = {
            'success': True, 'status': 'pending', 'amount': Decimal('50000'),
        }

        response = self.post(REMOTE_ADDR=TRUSTED_IP)

        self.assertEqual(response.json()['status'], 'ignored')
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('0'))

    @patch('main.payments.yookassa.YooKassaProvider')
    def test_amount_mismatch_is_rejected(self, provider):
        provider.return_value.check_payment.return_value = {
            'success': True, 'status': 'succeeded', 'amount': Decimal('1'),
        }

        response = self.post(REMOTE_ADDR=TRUSTED_IP)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('0'))

    @patch('main.payments.yookassa.YooKassaProvider')
    def test_confirmed_payment_credits_once(self, provider):
        provider.return_value.check_payment.return_value = {
            'success': True, 'status': 'succeeded', 'amount': Decimal('50000'),
        }

        self.post(REMOTE_ADDR=TRUSTED_IP)
        # ЮKassa повторяет доставку при таймауте — второй раз начислять нельзя
        self.post(REMOTE_ADDR=TRUSTED_IP)

        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('50000'))
        self.assertEqual(
            Transaction.objects.filter(receiver=self.user, kind='topup').count(), 1,
        )

    def test_crypto_webhook_endpoint_is_gone(self):
        # Крипто-вебхук брал сумму зачисления прямо из тела запроса
        response = self.client.post(
            '/payment/crypto-callback/', data='{}', content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)


# ==================== УТЕЧКИ ПЕРСОНАЛЬНЫХ ДАННЫХ ====================

class DataLeakTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user('alice', '8408')
        self.bob = make_user('bob', '100')
        self.client.force_login(self.bob)

    def test_api_users_hides_email_and_balance(self):
        body = self.client.get('/api/users/').content.decode()

        self.assertNotIn('alice@example.com', body)
        self.assertNotIn('8408', body)

    def test_api_leaders_requires_authentication(self):
        # Было permission_classes([AllowAny]) + балансы в ответе
        response = Client().get('/api/leaders/')
        self.assertIn(response.status_code, (401, 403))

    def test_api_leaders_does_not_expose_balances(self):
        services.transfer(self.bob, self.alice, Decimal('10'))
        response = self.client.get('/api/leaders/')

        for row in response.json()['top_senders']:
            self.assertNotIn('balance', row)

    def test_search_users_does_not_return_email(self):
        body = self.client.get('/api/search-users/?q=ali').content.decode()

        self.assertIn('alice', body)
        self.assertNotIn('alice@example.com', body)

    def test_foreign_profile_hides_balance(self):
        response = self.client.get(reverse('main:user_profile', args=['alice']))
        self.assertNotContains(response, '8408')

    def test_check_username_uses_exact_match(self):
        # icontains отвечал exists=true на любую подстроку
        self.assertFalse(self.client.get('/api/check-username/?username=a').json()['exists'])
        self.assertTrue(self.client.get('/api/check-username/?username=ALICE').json()['exists'])


class EncryptionTests(BaseCase):
    def test_passport_is_encrypted_at_rest(self):
        user = make_user('kyc_user')
        verification = UserVerification.objects.create(
            user=user, passport_series='1234', passport_number='567890',
        )

        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT passport_number FROM main_userverification WHERE id = %s',
                [verification.pk],
            )
            raw = cursor.fetchone()[0]

        self.assertNotIn('567890', raw)
        self.assertTrue(raw.startswith('enc:v1:'))
        self.assertEqual(
            UserVerification.objects.get(pk=verification.pk).passport_number, '567890',
        )

    def test_card_details_are_encrypted_at_rest(self):
        user = make_user('card_user', '5000')
        withdrawal = services.hold_for_withdrawal(
            user, Decimal('1000'), 'card',
            {'card_number': '4111111111111111', 'card_holder': 'IVAN IVANOV',
             'expiry_date': '12/29'},
            masked='****1111',
        )

        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT payment_details FROM main_withdrawalrequest WHERE id = %s',
                [withdrawal.pk],
            )
            raw = cursor.fetchone()[0]

        self.assertNotIn('4111111111111111', raw)
        self.assertEqual(
            WithdrawalRequest.objects.get(pk=withdrawal.pk).payment_details['card_number'],
            '4111111111111111',
        )


class KYCDocumentAccessTests(BaseCase):
    def test_document_not_readable_by_other_user(self):
        owner = make_user('doc_owner')
        stranger = make_user('stranger')
        document = KYCDocument.objects.create(
            user=owner, document_type='passport', document_number='1',
        )

        self.client.force_login(stranger)
        response = self.client.get(reverse('main:kyc_document', args=[document.pk]))
        self.assertEqual(response.status_code, 404)


# ==================== АУТЕНТИФИКАЦИЯ ====================

class AuthenticationTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('auth_user')

    def test_two_factor_is_enforced_on_login(self):
        TwoFactorAuth.objects.create(
            user=self.user, secret_key=TwoFactorAuthService.generate_secret(), is_enabled=True,
        )

        response = self.client.post(reverse('main:login'), {
            'username': 'auth_user', 'password': PASSWORD,
        })

        self.assertRedirects(response, reverse('main:login_2fa'))
        # Пароль принят, но сессия ещё не выдана
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_two_factor_completes_with_valid_code(self):
        import pyotp

        secret = TwoFactorAuthService.generate_secret()
        TwoFactorAuth.objects.create(user=self.user, secret_key=secret, is_enabled=True)

        self.client.post(reverse('main:login'), {'username': 'auth_user', 'password': PASSWORD})
        response = self.client.post(reverse('main:login_2fa'), {'code': pyotp.TOTP(secret).now()})

        self.assertRedirects(response, reverse('main:dashboard'))
        self.assertIn('_auth_user_id', self.client.session)

    def test_backup_code_works_once(self):
        two_factor = TwoFactorAuth.objects.create(
            user=self.user, secret_key=TwoFactorAuthService.generate_secret(),
            is_enabled=True, backup_codes=['aaaa1111', 'bbbb2222'],
        )

        self.assertTrue(TwoFactorAuthService.consume_backup_code(two_factor, 'aaaa1111'))
        self.assertFalse(TwoFactorAuthService.consume_backup_code(two_factor, 'aaaa1111'))

    @override_settings(LOGIN_MAX_ATTEMPTS=3)
    def test_brute_force_is_blocked(self):
        for _ in range(3):
            self.client.post(
                reverse('main:login'), {'username': 'auth_user', 'password': 'wrong'},
            )

        # Дальше даже верный пароль не пускает — сработала блокировка
        self.client.post(reverse('main:login'), {'username': 'auth_user', 'password': PASSWORD})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_failed_login_is_recorded(self):
        self.client.post(reverse('main:login'), {'username': 'auth_user', 'password': 'wrong'})
        log = SecurityLog.objects.filter(action='failed_login').first()

        self.assertIsNotNone(log)
        self.assertEqual(log.username_attempted, 'auth_user')

    def test_forwarded_for_header_cannot_spoof_ip(self):
        # Без доверенных прокси заголовок клиента игнорируется
        self.client.post(
            reverse('main:login'), {'username': 'auth_user', 'password': 'wrong'},
            HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='10.0.0.5',
        )
        self.assertEqual(SecurityLog.objects.first().ip_address, '10.0.0.5')


class ApiAuthTests(BaseCase):
    def test_api_register_rejects_weak_password(self):
        response = self.client.post('/api/auth/register/', {
            'username': 'weak', 'email': 'weak@example.com',
            'password': '12345678', 'password2': '12345678',
            'accept_terms': True, 'consent_data_processing': True,
        })
        self.assertEqual(response.status_code, 400)

    def test_api_register_requires_consents(self):
        response = self.client.post('/api/auth/register/', {
            'username': 'noconsent', 'email': 'nc@example.com',
            'password': PASSWORD, 'password2': PASSWORD,
            'accept_terms': True, 'consent_data_processing': False,
        })

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='noconsent').exists())

    def test_api_register_records_consents(self):
        response = self.client.post('/api/auth/register/', {
            'username': 'carol', 'email': 'carol@example.com',
            'password': PASSWORD, 'password2': PASSWORD,
            'accept_terms': True, 'consent_data_processing': True,
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(UserConsent.objects.filter(user__username='carol').count(), 3)

    def test_logout_revokes_refresh_token(self):
        make_user('logout_user')
        tokens = self.client.post(
            '/api/auth/login/', {'username': 'logout_user', 'password': PASSWORD},
        ).json()

        headers = {'HTTP_AUTHORIZATION': f'Bearer {tokens["access"]}'}
        logout = self.client.post('/api/auth/logout/', {'refresh': tokens['refresh']}, **headers)
        self.assertEqual(logout.status_code, 200)

        # Отозванный токен больше не обменивается на новый
        refresh = self.client.post('/api/auth/refresh/', {'refresh': tokens['refresh']})
        self.assertEqual(refresh.status_code, 401)


# ==================== ФУНКЦИОНАЛЬНЫЕ ДЕФЕКТЫ ====================

class BrokenPagesTests(BaseCase):
    """Страницы, падавшие с 500."""

    def setUp(self):
        super().setUp()
        self.user = make_user('pages_user', '1000')
        self.other = make_user('pages_other', '1000')
        self.client.force_login(self.user)

    def test_leaders_page_renders(self):
        services.transfer(self.user, self.other, Decimal('10'))
        self.assertEqual(self.client.get(reverse('main:leaders')).status_code, 200)

    def test_quick_help_page_renders(self):
        services.transfer(self.user, self.other, Decimal('10'))
        self.assertEqual(self.client.get(reverse('main:quick_help')).status_code, 200)

    def test_my_donations_page_renders(self):
        self.assertEqual(self.client.get(reverse('main:my_donations')).status_code, 200)

    def test_complete_fundraise_does_not_crash(self):
        # Было: NameError: floatformat — фильтр шаблонизатора внутри f-строки
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('1000'), author=self.user,
            status='active', moderation_status='approved',
        )
        # Завершение необратимо и принимается только POST: ссылку срабатывает
        # картинка на стороннем сайте
        self.assertEqual(
            self.client.get(reverse('main:complete_fundraise', args=[fundraise.pk])).status_code,
            405,
        )
        response = self.client.post(
            reverse('main:complete_fundraise', args=[fundraise.pk]), follow=True,
        )

        self.assertEqual(response.status_code, 200)
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.status, 'completed')

    def test_topup_with_non_numeric_amount_shows_error(self):
        # Было: decimal.InvalidOperation -> 500
        response = self.client.post(reverse('main:topup'), {'amount': 'abc'}, follow=True)
        self.assertEqual(response.status_code, 200)


class ApiMoneyOperationsTests(BaseCase):
    """API-переводы и пожертвования всегда отвечали 500 из-за Decimal/float."""

    def setUp(self):
        super().setUp()
        self.donor = make_user('api_donor', '5000')
        self.author = make_user('api_author')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )
        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(self.donor).access_token
        self.headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_api_transfer_succeeds(self):
        response = self.client.post('/api/transactions/transfer/', {
            'receiver_username': 'api_author', 'amount': '100',
        }, **self.headers)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Balance.objects.get(user=self.author).amount, Decimal('100'))

    def test_api_donate_succeeds(self):
        response = self.client.post(
            f'/api/fundraises/{self.fundraise.pk}/donate/', {'amount': '100'}, **self.headers,
        )

        self.assertEqual(response.status_code, 201)
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.current_amount, Decimal('100'))

    def test_api_transfer_insufficient_funds_returns_400(self):
        response = self.client.post('/api/transactions/transfer/', {
            'receiver_username': 'api_author', 'amount': '999999',
        }, **self.headers)

        self.assertEqual(response.status_code, 400)


class RemovedCryptoTests(BaseCase):
    def test_crypto_module_is_gone(self):
        with self.assertRaises(ImportError):
            import main.payments.crypto  # noqa: F401

    def test_crypto_models_are_gone(self):
        import main.models as models_module

        self.assertFalse(hasattr(models_module, 'CryptoBalance'))
        self.assertFalse(hasattr(models_module, 'CryptoTransaction'))


# ==================== ФИНАНСОВАЯ ЛОГИКА ====================

class MoneyIntegrityTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user('money_alice', '1000')
        self.bob = make_user('money_bob', '0')

    def test_transfer_moves_exact_amount(self):
        services.transfer(self.alice, self.bob, Decimal('250.55'))

        self.assertEqual(Balance.objects.get(user=self.alice).amount, Decimal('749.45'))
        self.assertEqual(Balance.objects.get(user=self.bob).amount, Decimal('250.55'))

    def test_transfer_rejects_insufficient_funds(self):
        with self.assertRaises(services.InsufficientFunds):
            services.transfer(self.alice, self.bob, Decimal('1000.01'))

        self.assertEqual(Balance.objects.get(user=self.alice).amount, Decimal('1000'))

    def test_transfer_to_self_is_rejected(self):
        with self.assertRaises(services.OperationRejected):
            services.transfer(self.alice, self.alice, Decimal('10'))

    def test_balance_cannot_go_negative_at_database_level(self):
        from django.db import IntegrityError, transaction as db_transaction

        balance = Balance.objects.get(user=self.alice)
        balance.amount = Decimal('-1')
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                balance.save()

    def test_donation_checks_balance_under_lock(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=self.bob,
            status='active', moderation_status='approved',
        )
        with self.assertRaises(services.InsufficientFunds):
            services.donate(self.alice, fundraise, Decimal('5000'))

        self.assertEqual(Balance.objects.get(user=self.alice).amount, Decimal('1000'))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.current_amount, Decimal('0'))

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('3'))
    def test_commission_is_credited_not_lost(self):
        """
        В API удерживались 3 %, которые никуда не зачислялись: донор списан
        на полную сумму, автор получил меньше, разница исчезала из системы.
        """
        service_account = self.service_account
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=self.bob,
            status='active', moderation_status='approved',
        )
        total_before = total_money()

        donation = services.donate(self.alice, fundraise, Decimal('100'))

        commission = CommissionTransaction.objects.get(donation=donation)
        self.assertEqual(commission.amount, Decimal('3.00'))
        self.assertEqual(Balance.objects.get(user=self.bob).amount, Decimal('97.00'))
        self.assertEqual(Balance.objects.get(user=service_account).amount, Decimal('3.00'))
        # Главный инвариант: внутренняя операция не меняет сумму всех балансов
        self.assertEqual(total_money(), total_before)

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('3'))
    def test_money_is_conserved_on_refund(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=self.bob,
            status='active', moderation_status='approved',
        )
        total_before = total_money()
        services.donate(self.alice, fundraise, Decimal('100'))

        services.refund_donations(fundraise)

        # Донору вернулось всё, включая комиссию: услуга не была оказана
        self.assertEqual(Balance.objects.get(user=self.alice).amount, Decimal('1000'))
        self.assertEqual(total_money(), total_before)

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('3'))
    def test_web_and_api_charge_identical_commission(self):
        """
        Оферта обещала 3 %, веб-версия брала 0 %, API — 3 % и терял их.
        Теперь обе точки входа проходят через один services.donate.
        """
        api_donor = make_user('same_api_donor', '1000')
        web_donor = make_user('same_web_donor', '1000')
        author = make_user('same_author', '0')
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=author,
            status='active', moderation_status='approved',
        )

        from rest_framework_simplejwt.tokens import RefreshToken
        token = RefreshToken.for_user(api_donor).access_token
        self.client.post(
            f'/api/fundraises/{fundraise.pk}/donate/', {'amount': '100'},
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        after_api = Balance.objects.get(user=author).amount

        self.client.force_login(web_donor)
        self.client.post(
            reverse('main:fundraise_detail', args=[fundraise.pk]), {'amount': '100'},
        )
        after_web = Balance.objects.get(user=author).amount

        self.assertEqual(after_api, Decimal('97.00'))
        self.assertEqual(after_web - after_api, Decimal('97.00'))


@override_settings(DONATION_COMMISSION_PERCENT=Decimal('0'))
class FundraiseCancellationTests(BaseCase):
    """
    Отмена сбора раньше оставляла собранные деньги у автора.

    Ставка комиссии зафиксирована нулём явно: тест проверяет механику
    возврата, а не текущее значение из окружения. Возврат с ненулевой
    комиссией проверяется отдельно в test_money_is_conserved_on_refund.
    """

    def setUp(self):
        super().setUp()
        self.donor = make_user('refund_donor', '1000')
        self.author = make_user('refund_author', '0')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=self.author,
            status='active', moderation_status='approved',
        )

    def test_cancel_refunds_donors(self):
        services.donate(self.donor, self.fundraise, Decimal('300'))
        self.assertEqual(Balance.objects.get(user=self.author).amount, Decimal('300'))

        self.client.force_login(self.author)
        self.client.post(reverse('main:cancel_fundraise', args=[self.fundraise.pk]))

        self.assertEqual(Balance.objects.get(user=self.donor).amount, Decimal('1000'))
        self.assertEqual(Balance.objects.get(user=self.author).amount, Decimal('0'))
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.status, 'cancelled')

    def test_cancel_requires_post(self):
        # GET-отмена работала бы как CSRF через чужую картинку
        self.client.force_login(self.author)
        response = self.client.get(reverse('main:cancel_fundraise', args=[self.fundraise.pk]))
        self.assertEqual(response.status_code, 405)

    def test_shortfall_is_reported_when_author_spent_money(self):
        services.donate(self.donor, self.fundraise, Decimal('300'))
        # Автор успел вывести часть средств
        Balance.objects.filter(user=self.author).update(amount=Decimal('100'))

        result = services.refund_donations(self.fundraise)

        self.assertEqual(result['refunded'], Decimal('100'))
        self.assertEqual(result['shortfall'], Decimal('200'))


class WithdrawalTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('wd_user', '100000')
        self.client.force_login(self.user)
        self.payload = {
            'payment_method': 'card', 'amount': '100000',
            'card_number': '4111111111111111', 'card_holder': 'IVAN IVANOV',
            'expiry_date': '12/29',
        }

    def test_unverified_user_cannot_exceed_limit(self):
        # Воспроизведено в аудите: заявка на 100 000 ₽ при лимите 500 ₽
        self.client.post(reverse('main:withdrawal'), self.payload, follow=True)

        self.assertFalse(WithdrawalRequest.objects.exists())
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))

    def test_withdrawal_within_limit_is_created(self):
        self.client.post(reverse('main:withdrawal'), dict(self.payload, amount='500'), follow=True)

        withdrawal = WithdrawalRequest.objects.get()
        self.assertEqual(withdrawal.amount, Decimal('500'))
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('99500'))
        self.assertEqual(withdrawal.payment_details_masked, '****1111')

    def test_invalid_card_checksum_is_rejected(self):
        payload = dict(self.payload, amount='500', card_number='4111111111111112')
        self.client.post(reverse('main:withdrawal'), payload, follow=True)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_failed_payout_refunds_user(self):
        # Было: статус 'failed' вне choices и деньги не возвращались
        withdrawal = services.hold_for_withdrawal(
            self.user, Decimal('500'), 'card', {'card_number': '4111111111111111'}, '****1111',
        )
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('99500'))

        withdrawal.mark_failed('платёжная система недоступна')

        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))
        self.assertEqual(withdrawal.status, 'failed')

    def test_double_refund_is_impossible(self):
        withdrawal = services.hold_for_withdrawal(
            self.user, Decimal('500'), 'card', {'card_number': '4111111111111111'}, '****1111',
        )
        self.assertTrue(withdrawal.cancel())
        self.assertFalse(withdrawal.cancel())

        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))


class VerificationTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('verify_user')
        self.client.force_login(self.user)

    def test_self_submitted_passport_does_not_grant_level(self):
        # Было: уровень 'basic' за произвольные «1234/567890»
        self.client.post(reverse('main:verification'), {
            'full_name': 'Иванов Иван', 'birth_date': '1990-01-01',
            'passport_series': '1234', 'passport_number': '567890',
        }, follow=True)

        verification = UserVerification.objects.get(user=self.user)
        self.assertEqual(verification.level, 'unverified')
        self.assertIsNotNone(verification.submitted_at)

    def test_payment_limit_is_enforced_for_unverified(self):
        from main.payments.verification import KYCService

        allowed, error = KYCService.check_payment_limit(self.user, Decimal('100000'))

        self.assertFalse(allowed)
        self.assertIn('Дневной лимит пополнения', error)


@override_settings(ALLOW_SIMULATED_TOPUP=True, SIMULATED_TOPUP_TOTAL_LIMIT=Decimal('100000'))
class SimulatedTopupTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('topup_user')
        self.client.force_login(self.user)

    def test_total_simulated_topup_is_capped(self):
        # Было: два запроса по 100 000 давали 200 000 ₽ из ничего
        self.client.post(reverse('main:topup'), {'amount': '100000'}, follow=True)
        self.client.post(reverse('main:topup'), {'amount': '100000'}, follow=True)

        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))


# ==================== АДМИНКА ====================

class AdminEscapingTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('root', 'root@example.com', PASSWORD)
        self.client.force_login(self.admin)

    def test_fundraise_title_is_escaped_in_admin(self):
        donor = make_user('xss_donor', '1000')
        author = make_user('xss_author')
        payload = '<img src=x onerror=alert(1)>'
        fundraise = Fundraise.objects.create(
            title=payload, description='.', category='other',
            target_amount=Decimal('100'), author=author,
            status='active', moderation_status='approved',
        )
        Donation.objects.create(donor=donor, fundraise=fundraise, amount=Decimal('1'))

        body = self.client.get('/admin/main/donation/').content.decode()

        self.assertNotIn(payload, body)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', body)

    def test_withdrawal_status_is_not_editable_in_list(self):
        from main.admin import WithdrawalRequestAdmin

        self.assertFalse(getattr(WithdrawalRequestAdmin, 'list_editable', None))


# ==================== ЮРИДИЧЕСКИЕ ДОКУМЕНТЫ ====================

class LegalDocumentsTests(BaseCase):
    """
    Документы раньше расходились с кодом: оферта обещала комиссию 3 %,
    политика перечисляла 5 видов данных из 15 собираемых и утверждала,
    что данные не передаются третьим лицам.
    """

    def test_all_documents_render(self):
        for name in ('privacy_policy', 'user_agreement', 'cookie_policy',
                     'consent_processing', 'consent_distribution', 'legal_details'):
            with self.subTest(document=name):
                self.assertEqual(self.client.get(reverse(f'main:{name}')).status_code, 200)

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('3'))
    def test_offer_shows_actual_commission_rate(self):
        # Ставка берётся из настроек, а не записана в тексте руками
        body = self.client.get(reverse('main:user_agreement')).content.decode()
        self.assertIn('3 %', body)

    def test_privacy_policy_lists_actually_collected_data(self):
        body = self.client.get(reverse('main:privacy_policy')).content.decode()

        for item in ('паспорт', 'карт', 'IP-адрес', 'ЮKassa'):
            with self.subTest(item=item):
                self.assertIn(item, body)

    def test_operator_details_are_published(self):
        # ч. 2 ст. 10 149-ФЗ: сведения о владельце сайта
        from django.conf import settings

        body = self.client.get(reverse('main:legal_details')).content.decode()
        self.assertIn(settings.OPERATOR['inn'], body)
        self.assertIn('18+', body)

    def test_unfilled_operator_details_are_flagged(self):
        # Документ с плейсхолдерами не должен выглядеть готовым
        body = self.client.get(reverse('main:privacy_policy')).content.decode()
        if '[' in settings_operator_name():
            self.assertIn('Документ не заполнен', body)


def settings_operator_name():
    from django.conf import settings
    return settings.OPERATOR['name']


class RegistrationConsentTests(BaseCase):
    """Ст. 9 152-ФЗ с 01.09.2025: согласие на ПДн — отдельно от оферты."""

    def _payload(self, **overrides):
        data = {
            'username': 'newcomer', 'email': 'newcomer@example.com',
            'password1': PASSWORD, 'password2': PASSWORD,
            'accept_terms': 'on', 'consent_data_processing': 'on',
        }
        data.update(overrides)
        return data

    def test_registration_requires_data_processing_consent(self):
        payload = self._payload()
        payload.pop('consent_data_processing')
        self.client.post(reverse('main:register'), payload)

        self.assertFalse(User.objects.filter(username='newcomer').exists())

    def test_registration_records_separate_consents(self):
        self.client.post(reverse('main:register'), self._payload())

        types = set(
            UserConsent.objects.filter(user__username='newcomer')
            .values_list('consent_type', flat=True)
        )
        self.assertIn('terms', types)
        self.assertIn('data_processing', types)
        # Распространение — необязательное, не отмечали
        self.assertNotIn('distribution', types)

    def test_distribution_consent_is_optional_and_recorded(self):
        self.client.post(
            reverse('main:register'), self._payload(consent_distribution='on'),
        )

        types = set(
            UserConsent.objects.filter(user__username='newcomer')
            .values_list('consent_type', flat=True)
        )
        self.assertIn('distribution', types)


# ==================== ПРАВА СУБЪЕКТА ПДн ====================

class DataSubjectRightsTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('subject', '0')
        UserVerification.objects.create(
            user=self.user, full_name='Иванов Иван', passport_series='1234',
            passport_number='567890',
        )
        self.client.force_login(self.user)

    def test_export_returns_own_data(self):
        response = self.client.get(reverse('main:export_my_data'))
        payload = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(payload['учётная_запись']['имя_пользователя'], 'subject')
        # Паспорт отдаётся расшифрованным — это данные самого пользователя
        self.assertEqual(payload['верификация']['паспорт_номер'], '567890')

    def test_delete_account_destroys_personal_data(self):
        user_id = self.user.pk
        response = self.client.post(
            reverse('main:delete_account'), {'confirm': 'subject'}, follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserVerification.objects.filter(user_id=user_id).exists())

        user = User.objects.get(pk=user_id)
        self.assertEqual(user.email, '')
        self.assertFalse(user.is_active)
        self.assertNotEqual(user.username, 'subject')

    def test_delete_requires_exact_username(self):
        self.client.post(reverse('main:delete_account'), {'confirm': 'wrong'})
        self.assertTrue(UserVerification.objects.filter(user=self.user).exists())

    def test_delete_blocked_while_balance_is_not_empty(self):
        Balance.objects.filter(user=self.user).update(amount=Decimal('100'))

        self.client.post(reverse('main:delete_account'), {'confirm': 'subject'})

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.username, 'subject')

    def test_delete_blocked_while_fundraise_is_active(self):
        Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('1000'), author=self.user, status='active',
            moderation_status='approved',
        )

        self.client.post(reverse('main:delete_account'), {'confirm': 'subject'})

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_delete_requires_post(self):
        response = self.client.get(reverse('main:delete_account'))
        self.assertEqual(response.status_code, 405)


# ==================== НАХОДКИ ПОВТОРНОЙ ПРОВЕРКИ ====================

class DeadAccountTests(BaseCase):
    """Деньги не должны уходить туда, откуда их никто не заберёт."""

    def setUp(self):
        super().setUp()
        self.sender = make_user('dead_sender', '1000')
        self.receiver = make_user('dead_receiver', '0')

    def test_transfer_to_deleted_account_is_rejected(self):
        self.receiver.is_active = False
        self.receiver.save(update_fields=['is_active'])

        with self.assertRaises(services.OperationRejected):
            services.transfer(self.sender, self.receiver, Decimal('100'))

    def test_transfer_to_service_account_is_rejected(self):
        with self.assertRaises(services.OperationRejected):
            services.transfer(self.sender, self.service_account, Decimal('100'))

    def test_donation_to_deleted_author_is_rejected(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('1000'), author=self.receiver,
            status='active', moderation_status='approved',
        )
        self.receiver.is_active = False
        self.receiver.save(update_fields=['is_active'])

        with self.assertRaises(services.OperationRejected):
            services.donate(self.sender, fundraise, Decimal('100'))


class AccountDeletionGuardsTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('guarded', '0')
        self.client.force_login(self.user)

    def test_deletion_uses_unique_label(self):
        # Логин deleted_<pk> можно было занять заранее и заблокировать удаление
        User.objects.create_user(username=f'deleted_{self.user.pk}')

        self.client.post(reverse('main:delete_account'), {'confirm': 'guarded'})

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertNotEqual(self.user.username, 'guarded')

    def test_deletion_blocked_while_payment_pending(self):
        # Иначе вебхук зачислил бы деньги на удалённый аккаунт
        PaymentTransaction.objects.create(
            user=self.user, amount=Decimal('5000'), payment_method='card',
            payment_id='pending-1', status='pending',
        )

        self.client.post(reverse('main:delete_account'), {'confirm': 'guarded'})

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_deletion_hides_fundraise_content(self):
        # Описание сбора может содержать сведения о здоровье третьих лиц
        Fundraise.objects.create(
            title='Лечение Иванова И.И.', description='диагноз', category='medical',
            target_amount=Decimal('1000'), author=self.user, status='completed',
            moderation_status='approved',
        )

        self.client.post(reverse('main:delete_account'), {'confirm': 'guarded'})

        fundraise = Fundraise.objects.get(author=self.user)
        self.assertEqual(fundraise.title, 'Сбор удалён')
        self.assertEqual(fundraise.description, '')

    def test_deletion_clears_log_ip_addresses(self):
        SecurityLog.objects.create(
            user=self.user, action='login', ip_address='203.0.113.9',
            user_agent='Browser/1.0', details={'x': 'y'},
        )

        self.client.post(reverse('main:delete_account'), {'confirm': 'guarded'})

        log = SecurityLog.objects.filter(action='login').first()
        self.assertIsNone(log.user)
        self.assertIsNone(log.ip_address)
        self.assertEqual(log.details, {})


class AdminFundraiseCancellationTests(BaseCase):
    """Админская отмена сбора обходила возврат средств."""

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('0'))
    def test_admin_cancel_refunds_donors(self):
        from django.contrib.admin.sites import AdminSite

        from main.admin import FundraiseAdmin

        donor = make_user('admin_donor', '1000')
        author = make_user('admin_author', '0')
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('5000'), author=author,
            status='active', moderation_status='approved',
        )
        services.donate(donor, fundraise, Decimal('400'))

        admin_user = User.objects.create_superuser('adminuser', 'a@e.ru', PASSWORD)
        request = self.client.request().wsgi_request
        request.user = admin_user
        # message_user требует сообщений в запросе
        from django.contrib.messages.storage.fallback import FallbackStorage
        request.session = self.client.session
        request._messages = FallbackStorage(request)

        FundraiseAdmin(Fundraise, AdminSite()).cancel_fundraises(
            request, Fundraise.objects.filter(pk=fundraise.pk),
        )

        self.assertEqual(Balance.objects.get(user=donor).amount, Decimal('1000'))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.status, 'cancelled')


class ConsentIntegrityTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('consent_user', '0')
        self.client.force_login(self.user)

    def test_reaccept_does_not_crash_on_existing_consent(self):
        # Было: IntegrityError из-за unique_together
        from main.views import record_consents

        request = self.client.request().wsgi_request
        record_consents(request, self.user, ['cookies'])

        response = self.client.post(reverse('main:my_consents'), {
            'action': 'reaccept', 'consent_type': 'cookies',
        })
        self.assertEqual(response.status_code, 302)

    def test_arbitrary_consent_type_is_rejected(self):
        self.client.post(reverse('main:my_consents'), {
            'action': 'reaccept', 'consent_type': 'ВЗЛОМ', 'version': '9.9',
        })
        self.assertFalse(UserConsent.objects.filter(consent_type='ВЗЛОМ').exists())

    def test_required_consent_cannot_be_revoked_silently(self):
        from main.views import record_consents

        request = self.client.request().wsgi_request
        record_consents(request, self.user, ['data_processing'])

        self.client.post(reverse('main:my_consents'), {
            'action': 'revoke', 'consent_type': 'data_processing',
        })

        # Согласие осталось: отзыв требует удаления учётной записи
        self.assertTrue(
            UserConsent.has_active_consent(self.user, 'data_processing')
        )


class FundraiseConsentTests(BaseCase):
    """Публикация сбора требует согласия по ст. 10.1 152-ФЗ."""

    def setUp(self):
        super().setUp()
        self.user = make_user('author_user', '0')
        self.client.force_login(self.user)

    def test_cannot_create_fundraise_without_distribution_consent(self):
        response = self.client.post(reverse('main:create_fundraise'), {
            'title': 'Сбор', 'description': '.', 'category': 'other',
            'target_amount': '1000',
        })

        self.assertRedirects(response, reverse('main:my_consents'))
        self.assertFalse(Fundraise.objects.filter(author=self.user).exists())

    def test_can_create_fundraise_with_consent(self):
        from main.views import record_consents

        request = self.client.request().wsgi_request
        record_consents(request, self.user, ['distribution'])

        self.client.post(reverse('main:create_fundraise'), {
            'title': 'Сбор', 'description': '.', 'category': 'other',
            'target_amount': '1000',
        })

        self.assertTrue(Fundraise.objects.filter(author=self.user).exists())


class WithdrawalDetailsRetentionTests(BaseCase):
    def test_details_are_erased_after_payout(self):
        # Политика обещает, что после выплаты остаётся только маска
        user = make_user('payout_user', '5000')
        admin_user = User.objects.create_superuser('payout_admin', 'p@e.ru', PASSWORD)

        withdrawal = services.hold_for_withdrawal(
            user, Decimal('500'), 'card',
            {'card_number': '4111111111111111'}, '****1111',
        )
        withdrawal.approve(admin_user)
        withdrawal.complete(admin_user, transaction_id='payout-1')

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.payment_details, {})
        self.assertEqual(withdrawal.payment_details_masked, '****1111')


class RetentionCommandTests(BaseCase):
    def test_command_removes_expired_security_logs(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        user = make_user('retention_user')
        log = SecurityLog.objects.create(user=user, action='login', ip_address='1.2.3.4')
        SecurityLog.objects.filter(pk=log.pk).update(
            created_at=timezone.now() - timedelta(days=400),
        )

        call_command('enforce_retention')

        self.assertFalse(SecurityLog.objects.filter(pk=log.pk).exists())

    def test_dry_run_changes_nothing(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        user = make_user('retention_dry')
        log = SecurityLog.objects.create(user=user, action='login', ip_address='1.2.3.4')
        SecurityLog.objects.filter(pk=log.pk).update(
            created_at=timezone.now() - timedelta(days=400),
        )

        call_command('enforce_retention', '--dry-run')

        self.assertTrue(SecurityLog.objects.filter(pk=log.pk).exists())


class ExternalResourcesTests(BaseCase):
    """
    Внешних ресурсов на страницах быть не должно.

    Раньше каждая страница — включая саму Политику конфиденциальности —
    грузила шрифты с fonts.googleapis.com и значки с cdnjs.cloudflare.com,
    отправляя IP-адрес и User-Agent посетителя в США. Это трансграничная
    передача персональных данных (ст. 12 152-ФЗ) без уведомления РКН.
    """

    EXTERNAL_HOSTS = ('fonts.googleapis.com', 'fonts.gstatic.com',
                      'cdnjs.cloudflare.com', 'cdn.jsdelivr.net', 'unpkg.com')

    def test_no_external_hosts_in_any_template(self):
        """
        Проверяются сами шаблоны, а не отрендеренные страницы: так тест
        поймает внешнюю ссылку на любой странице, включая те, которые
        он не открывает.
        """
        import os

        from django.conf import settings

        offenders = []
        for root, _dirs, files in os.walk(settings.BASE_DIR / 'main' / 'templates'):
            for name in files:
                if not name.endswith('.html'):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding='utf-8') as handle:
                    text = handle.read()
                for host in self.EXTERNAL_HOSTS:
                    # Упоминание в тексте документа — не загрузка ресурса
                    if f'//{host}' in text:
                        offenders.append(f'{path}: {host}')
        self.assertEqual(offenders, [], 'Внешние ресурсы в шаблонах: ' + '; '.join(offenders))

    def test_rendered_pages_request_nothing_outside(self):
        make_user('external_probe')
        self.client.login(username='external_probe', password=PASSWORD)
        for name in ('main:privacy_policy', 'main:cookie_policy', 'main:dashboard',
                     'main:fundraises', 'main:consent_processing'):
            body = self.client.get(reverse(name)).content.decode()
            for host in self.EXTERNAL_HOSTS:
                self.assertNotIn(f'//{host}', body, f'{name} обращается к {host}')

    def test_policy_states_transfer_does_not_happen(self):
        body = self.client.get(reverse('main:privacy_policy')).content.decode()
        self.assertIn('не осуществляется', body)

    def test_vendored_files_exist(self):
        """Без них страницы остались бы без шрифтов и значков вовсе."""
        from django.conf import settings

        vendor = settings.BASE_DIR / 'main' / 'static' / 'vendor'
        self.assertTrue((vendor / 'fonts' / 'fonts.css').exists())
        self.assertTrue((vendor / 'fontawesome' / 'css' / 'all.min.css').exists())
        self.assertTrue((vendor / 'fontawesome' / 'webfonts' / 'fa-solid-900.woff2').exists())

    def test_vendored_css_has_no_external_urls(self):
        from django.conf import settings

        vendor = settings.BASE_DIR / 'main' / 'static' / 'vendor'
        for css in (vendor / 'fonts' / 'fonts.css',
                    vendor / 'fontawesome' / 'css' / 'all.min.css'):
            text = css.read_text(encoding='utf-8')
            for host in self.EXTERNAL_HOSTS:
                # Ищется ссылка, а не упоминание: в шапке файла объяснено,
                # откуда эти шрифты взялись, и это не загрузка
                self.assertNotIn(f'//{host}', text, f'{css.name} ссылается на {host}')


# ==================== МОДЕРАЦИЯ СБОРОВ ====================

def give_distribution_consent(user):
    UserConsent.objects.create(
        user=user, consent_type='distribution',
        version=UserConsent.current_version(), is_accepted=True,
    )


LONG_DESCRIPTION = 'Подробное описание цели сбора. ' * 12


class FundraiseModerationTests(BaseCase):
    """
    До этих правил сбор публиковался мгновенно: любой заводил сбор на
    10 000 000 ₽ «на лечение» без документов и сразу принимал деньги
    с витрины площадки.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('mod_author')
        self.donor = make_user('mod_donor', balance='1000')
        self.moderator = User.objects.create_user(
            username='moderator', email='m@example.com', password=PASSWORD, is_staff=True,
        )
        give_distribution_consent(self.author)
        self.client.login(username='mod_author', password=PASSWORD)

    def make_draft(self, **kwargs):
        params = dict(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='draft', moderation_status='draft',
        )
        params.update(kwargs)
        return Fundraise.objects.create(**params)

    def test_created_fundraise_is_draft_not_public(self):
        response = self.client.post(reverse('main:create_fundraise'), {
            'title': 'Новый сбор', 'description': LONG_DESCRIPTION,
            'category': 'other', 'target_amount': '10000',
        })
        fundraise = Fundraise.objects.get(title='Новый сбор')
        self.assertEqual(fundraise.status, 'draft')
        self.assertEqual(fundraise.moderation_status, 'draft')
        self.assertFalse(fundraise.is_public)
        self.assertFalse(fundraise.accepts_donations)
        self.assertEqual(response.status_code, 302)

    def test_unmoderated_fundraise_absent_from_listing(self):
        self.make_draft(title='Сбор-без-проверки-77')
        self.client.login(username='mod_donor', password=PASSWORD)
        body = self.client.get(reverse('main:fundraises')).content.decode()
        self.assertNotIn('Сбор-без-проверки-77', body)

    def test_stranger_cannot_open_unmoderated_fundraise_by_direct_link(self):
        """Скрыть из списка мало: ссылку автор разослал бы сам."""
        fundraise = self.make_draft()
        self.client.login(username='mod_donor', password=PASSWORD)
        response = self.client.get(reverse('main:fundraise_detail', args=[fundraise.pk]))
        self.assertEqual(response.status_code, 404)

    def test_donation_to_unmoderated_fundraise_rejected_in_service(self):
        """Проверка обязана быть в services.donate: туда же приходит API."""
        fundraise = self.make_draft()
        with self.assertRaises(services.OperationRejected):
            services.donate(self.donor, fundraise, Decimal('100'))
        self.assertEqual(Balance.objects.get(user=self.donor).amount, Decimal('1000'))

    def test_submit_requires_distribution_consent(self):
        UserConsent.objects.filter(user=self.author).delete()
        fundraise = self.make_draft()
        problems = moderation.check_can_submit(fundraise)
        self.assertTrue(any('распространение' in p for p in problems))

    def test_large_amount_requires_verification(self):
        fundraise = self.make_draft(target_amount=Decimal('400000'))
        problems = moderation.check_can_submit(fundraise)
        self.assertTrue(any('верификация' in p for p in problems))

    def test_medical_category_requires_document(self):
        fundraise = self.make_draft(category='medical')
        problems = moderation.check_can_submit(fundraise)
        self.assertTrue(any('документ' in p for p in problems))

    def test_short_description_blocks_submission(self):
        fundraise = self.make_draft(description='Помогите')
        problems = moderation.check_can_submit(fundraise)
        self.assertTrue(any('подробнее' in p for p in problems))

    def test_submit_moves_to_pending_and_locks_editing(self):
        fundraise = self.make_draft()
        self.client.post(reverse('main:submit_fundraise', args=[fundraise.pk]))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.moderation_status, 'pending')
        self.assertIsNotNone(fundraise.submitted_at)
        self.assertFalse(fundraise.is_editable)

    def test_approval_publishes_and_opens_donations(self):
        fundraise = self.make_draft()
        fundraise.submit_for_moderation()

        self.client.login(username='moderator', password=PASSWORD)
        self.client.post(reverse('main:moderate_fundraise', args=[fundraise.pk]),
                         {'action': 'approve', 'comment': ''})

        fundraise.refresh_from_db()
        self.assertEqual(fundraise.moderation_status, 'approved')
        self.assertEqual(fundraise.status, 'active')
        self.assertTrue(fundraise.accepts_donations)
        self.assertEqual(fundraise.moderated_by, self.moderator)

        services.donate(self.donor, fundraise, Decimal('100'))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.current_amount, Decimal('100'))

    def test_request_changes_requires_comment(self):
        fundraise = self.make_draft()
        fundraise.submit_for_moderation()
        self.client.login(username='moderator', password=PASSWORD)
        self.client.post(reverse('main:moderate_fundraise', args=[fundraise.pk]),
                         {'action': 'request_changes', 'comment': '  '})
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.moderation_status, 'pending')

    def test_rejection_refunds_donors(self):
        """Отклонённый сбор не вправе удерживать собранное."""
        fundraise = self.make_draft(status='active', moderation_status='approved')
        services.donate(self.donor, fundraise, Decimal('200'))
        donor_after_donation = Balance.objects.get(user=self.donor).amount

        self.client.login(username='moderator', password=PASSWORD)
        self.client.post(reverse('main:moderate_fundraise', args=[fundraise.pk]),
                         {'action': 'reject', 'comment': 'Не подтверждена цель'})

        fundraise.refresh_from_db()
        self.assertEqual(fundraise.moderation_status, 'rejected')
        self.assertEqual(fundraise.status, 'cancelled')
        self.assertGreater(Balance.objects.get(user=self.donor).amount, donor_after_donation)

    def test_editing_approved_fundraise_returns_it_to_moderation(self):
        """
        Иначе одобрение получают на безобидном тексте, а сразу после
        подменяют его — проверка становится формальностью.
        """
        fundraise = self.make_draft(status='active', moderation_status='approved')
        self.client.post(reverse('main:fundraise_manage', args=[fundraise.pk]), {
            'title': 'Другая цель', 'description': LONG_DESCRIPTION,
            'category': 'other', 'target_amount': '10000',
        })
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.title, 'Другая цель')
        self.assertEqual(fundraise.moderation_status, 'draft')
        self.assertFalse(fundraise.accepts_donations)

    def test_non_staff_cannot_open_moderation_queue(self):
        response = self.client.get(reverse('main:moderation_queue'))
        self.assertEqual(response.status_code, 404)

    def test_second_unfinished_fundraise_blocked(self):
        self.make_draft()
        self.client.get(reverse('main:create_fundraise'), follow=True)
        self.client.post(reverse('main:create_fundraise'), {
            'title': 'Второй', 'description': LONG_DESCRIPTION,
            'category': 'other', 'target_amount': '1000',
        })
        self.assertFalse(Fundraise.objects.filter(title='Второй').exists())

    def test_featuring_unmoderated_fundraise_is_skipped(self):
        """«Рекомендуемый» означает, что площадка за сбор ручается."""
        from django.contrib.admin.sites import AdminSite

        from main.admin import FundraiseAdmin

        fundraise = self.make_draft()
        admin_instance = FundraiseAdmin(Fundraise, AdminSite())
        request = self.client.request().wsgi_request
        request._messages = FakeMessages()
        admin_instance.feature_fundraises(request, Fundraise.objects.filter(pk=fundraise.pk))
        fundraise.refresh_from_db()
        self.assertFalse(fundraise.is_featured)


class FakeMessages(list):
    """Подмена message_user: в юнит-тесте нет middleware сообщений."""

    def add(self, level, message, extra_tags=''):
        self.append(message)


class PersonalDataAccessLogTests(BaseCase):
    """
    Политика обещала журналирование доступа сотрудников к ПДн, а Django
    пишет в LogEntry только изменения — просмотр паспорта не фиксировался
    нигде.
    """

    def setUp(self):
        super().setUp()
        self.subject = make_user('pd_subject')
        self.staff = User.objects.create_user(
            username='pd_staff', email='s@example.com', password=PASSWORD, is_staff=True,
        )
        give_distribution_consent(self.subject)
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='medical',
            target_amount=Decimal('1000'), author=self.subject,
            status='draft', moderation_status='pending',
        )
        self.document = FundraiseDocument.objects.create(
            fundraise=self.fundraise, document_type='medical',
            file=SimpleUploadedFile('spravka.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )

    def test_staff_download_is_logged(self):
        self.client.login(username='pd_staff', password=PASSWORD)
        response = self.client.get(reverse('main:fundraise_document', args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)

        entry = PersonalDataAccessLog.objects.get()
        self.assertEqual(entry.actor, self.staff)
        self.assertEqual(entry.subject, self.subject)
        self.assertEqual(entry.data_type, 'fundraise_document')

    def test_owner_download_is_not_logged(self):
        """Журнал фиксирует доступ сотрудников, а не автора к своему файлу."""
        self.client.login(username='pd_subject', password=PASSWORD)
        self.client.get(reverse('main:fundraise_document', args=[self.document.pk]))
        self.assertEqual(PersonalDataAccessLog.objects.count(), 0)

    def test_stranger_cannot_download_document(self):
        make_user('pd_stranger')
        self.client.login(username='pd_stranger', password=PASSWORD)
        response = self.client.get(reverse('main:fundraise_document', args=[self.document.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PersonalDataAccessLog.objects.count(), 0)


# ==================== ОГРАНИЧЕНИЯ ПО УЧЁТНЫМ ЗАПИСЯМ ====================

class AccountRestrictionTests(BaseCase):
    """
    Раздел 9 оферты описывал приостановление операций со сроками, но в коде
    его не было: «блокировка» сводилась к is_active=False — без причины,
    без уведомления и без возможности возразить.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('restricted_user', balance='1000')
        self.other = make_user('other_user', balance='0')
        self.staff = User.objects.create_user(
            username='restr_staff', email='rs@example.com', password=PASSWORD, is_staff=True,
        )

    def restrict(self, **kwargs):
        params = dict(
            user=self.user, kind='suspended', ground='fraud_suspicion',
            reason='Признаки использования чужой карты.', actor=self.staff,
        )
        params.update(kwargs)
        return restrictions_service.apply_restriction(**params)

    def test_restriction_blocks_outgoing_transfer(self):
        self.restrict()
        with self.assertRaises(services.OperationRejected):
            services.transfer(self.user, self.other, Decimal('100'))
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('1000'))

    def test_restriction_does_not_touch_the_balance(self):
        """П. 9.4: ограничение не влечёт утрату права на средства."""
        self.restrict()
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('1000'))

    def test_incoming_transfer_still_works(self):
        """Ограничиваются расходные операции, а не право получить своё."""
        self.restrict()
        make_user('payer', balance='500')
        services.transfer(User.objects.get(username='payer'), self.user, Decimal('100'))
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('1100'))

    def test_restriction_blocks_withdrawal(self):
        self.restrict()
        with self.assertRaises(services.OperationRejected):
            services.hold_for_withdrawal(
                self.user, Decimal('100'), 'card', {'card': '1'}, '**** 0001',
            )

    def test_user_is_notified_with_reason(self):
        """П. 9.2: уведомление в течение 1 рабочего дня, с причиной."""
        restriction = self.restrict()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Признаки использования чужой карты', mail.outbox[0].body)
        self.assertIsNotNone(restriction.notified_at)
        self.assertLessEqual(restriction.notified_at, restriction.notify_deadline)

    def test_reason_is_mandatory(self):
        """«Нарушение правил» без объяснения лишает смысла право возразить."""
        with self.assertRaises(ValueError):
            self.restrict(reason='   ')

    def test_appeal_sets_five_business_day_deadline(self):
        restriction = self.restrict()
        self.client.login(username='restricted_user', password=PASSWORD)
        self.client.post(reverse('main:my_restrictions'),
                         {'appeal': 'Карта моя, приложил выписку банка.'})

        restriction.refresh_from_db()
        self.assertIsNotNone(restriction.appeal_submitted_at)
        self.assertEqual(
            restriction.appeal_deadline,
            add_business_days(restriction.appeal_submitted_at, 5),
        )
        self.assertGreaterEqual(
            (restriction.appeal_deadline - restriction.appeal_submitted_at).days, 5,
        )

    def test_lifting_restores_operations(self):
        restriction = self.restrict()
        restriction.resolve(self.staff, 'lifted', 'Документы подтверждают принадлежность карты.')
        services.transfer(self.user, self.other, Decimal('100'))
        self.assertEqual(Balance.objects.get(user=self.other).amount, Decimal('100'))

    def test_decision_requires_motivation(self):
        restriction = self.restrict()
        restriction.submit_appeal('Объяснения пользователя по существу.')
        self.client.login(username='restr_staff', password=PASSWORD)
        self.client.post(reverse('main:resolve_restriction', args=[restriction.pk]),
                         {'decision': 'upheld', 'comment': ''})
        restriction.refresh_from_db()
        self.assertEqual(restriction.decision, '')

    def test_restricted_author_fundraise_leaves_the_listing(self):
        fundraise = Fundraise.objects.create(
            title='Сбор ограниченного автора', description='.', category='other',
            target_amount=Decimal('1000'), author=self.user,
            status='active', moderation_status='approved',
        )
        self.assertIn(fundraise, Fundraise.published())
        self.restrict()
        self.assertNotIn(fundraise, Fundraise.published())

    def test_donation_to_restricted_author_rejected(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description='.', category='other',
            target_amount=Decimal('1000'), author=self.user,
            status='active', moderation_status='approved',
        )
        self.restrict()
        donor = make_user('restr_donor', balance='500')
        with self.assertRaises(services.OperationRejected):
            services.donate(donor, fundraise, Decimal('100'))

    def test_notification_overdue_is_visible(self):
        """Пропущенный срок должен быть виден, а не потерян."""
        restriction = self.restrict()
        restriction.notified_at = None
        restriction.notify_deadline = timezone.now() - timezone.timedelta(days=1)
        restriction.save(update_fields=['notified_at', 'notify_deadline'])
        self.assertTrue(restriction.notification_overdue)


# ==================== ИНЦИДЕНТЫ С ПЕРСОНАЛЬНЫМИ ДАННЫМИ ====================

class DataBreachTests(BaseCase):
    """
    Политика обещала уведомление РКН за 24 и 72 часа (ч. 3.1 ст. 21 152-ФЗ),
    но ни журнала инцидентов, ни отсчёта сроков не существовало.
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username='breach_staff', email='b@example.com', password=PASSWORD, is_staff=True,
        )

    def make_incident(self, hours_ago=0, **kwargs):
        params = dict(
            detected_at=timezone.now() - timezone.timedelta(hours=hours_ago),
            summary='Доступ к резервной копии',
            description='Резервная копия оказалась доступна без аутентификации.',
            data_categories='паспортные данные, платёжные реквизиты',
            affected_count=21,
        )
        params.update(kwargs)
        return DataBreachIncident.objects.create(**params)

    def test_deadlines_counted_from_detection(self):
        incident = self.make_incident()
        self.assertEqual(
            incident.initial_notice_deadline,
            incident.detected_at + timezone.timedelta(hours=24),
        )
        self.assertEqual(
            incident.final_notice_deadline,
            incident.detected_at + timezone.timedelta(hours=72),
        )

    def test_deadlines_move_when_detection_time_is_corrected(self):
        """Иначе журнал показывал бы соблюдение срока, которого не было."""
        incident = self.make_incident()
        incident.detected_at = incident.detected_at - timezone.timedelta(hours=10)
        incident.save()
        self.assertEqual(
            incident.initial_notice_deadline,
            incident.detected_at + timezone.timedelta(hours=24),
        )

    def test_overdue_is_detected(self):
        incident = self.make_incident(hours_ago=30)
        self.assertTrue(incident.initial_overdue)
        self.assertFalse(incident.final_overdue)

        incident = self.make_incident(hours_ago=80)
        self.assertTrue(incident.final_overdue)

    def test_sent_notice_closes_the_deadline(self):
        incident = self.make_incident(hours_ago=30)
        incident.initial_notice_sent_at = incident.detected_at + timezone.timedelta(hours=2)
        incident.initial_notice_reference = 'РКН-123'
        incident.save()
        self.assertFalse(incident.initial_overdue)

    def test_incident_closes_only_after_both_notices(self):
        incident = self.make_incident()
        self.assertTrue(incident.is_open)
        incident.initial_notice_sent_at = timezone.now()
        incident.save()
        self.assertTrue(incident.is_open)
        incident.final_notice_sent_at = timezone.now()
        incident.save()
        self.assertFalse(incident.is_open)

    def test_breach_check_reports_open_incidents(self):
        self.make_incident(hours_ago=30)
        out = StringIO()
        call_command('breach_check', stdout=out)
        self.assertIn('СРОК ПРОПУЩЕН', out.getvalue())

    def test_draft_contains_mandatory_details(self):
        incident = self.make_incident()
        out = StringIO()
        call_command('breach_check', draft=incident.pk, stdout=out)
        text = out.getvalue()
        self.assertIn('паспортные данные', text)
        self.assertIn('21', text)  # число затронутых субъектов
        self.assertIn('152-ФЗ', text)

    def test_procedure_page_is_staff_only(self):
        make_user('breach_user')
        self.client.login(username='breach_user', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('main:breach_procedure')).status_code, 404)

        self.client.login(username='breach_staff', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('main:breach_procedure')).status_code, 200)


# ==================== ОБХОДНЫЕ ПУТИ, НАЙДЕННЫЕ ПРОВЕРКОЙ ====================

class ApiModerationBypassTests(BaseCase):
    """
    API знал только Fundraise.objects.all() и обходил модерацию целиком:
    отдавал чужие черновики, позволял создать сбор без согласия на
    распространение ПДн, править одобренный текст и удалять сбор
    вместе с пожертвованиями.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('api_author')
        self.stranger = make_user('api_stranger', balance='1000')
        give_distribution_consent(self.author)
        self.draft = Fundraise.objects.create(
            title='Черновик-API-88', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='draft', moderation_status='draft',
        )

    def auth(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = RefreshToken.for_user(user).access_token
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_stranger_does_not_see_unmoderated_fundraise(self):
        headers = self.auth(self.stranger)
        listing = self.client.get('/api/fundraises/', **headers).json()
        titles = [item['title'] for item in listing.get('results', listing)]
        self.assertNotIn('Черновик-API-88', titles)
        self.assertEqual(
            self.client.get(f'/api/fundraises/{self.draft.pk}/', **headers).status_code, 404,
        )

    def test_author_sees_own_draft(self):
        response = self.client.get(f'/api/fundraises/{self.draft.pk}/', **self.auth(self.author))
        self.assertEqual(response.status_code, 200)

    def test_editing_approved_fundraise_via_api_resets_moderation(self):
        self.draft.approve(make_user('api_moderator'))
        self.client.patch(
            f'/api/fundraises/{self.draft.pk}/',
            data=json.dumps({'title': 'Инвестиции под 300% годовых'}),
            content_type='application/json', **self.auth(self.author),
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, 'Инвестиции под 300% годовых')
        self.assertNotEqual(self.draft.moderation_status, 'approved')
        self.assertFalse(self.draft.accepts_donations)

    def test_fundraise_cannot_be_deleted_via_api(self):
        """Удаление уносило пожертвования и само обязательство их вернуть."""
        self.draft.approve(make_user('api_moderator2'))
        services.donate(self.stranger, self.draft, Decimal('100'))

        response = self.client.delete(
            f'/api/fundraises/{self.draft.pk}/', **self.auth(self.author),
        )
        self.assertIn(response.status_code, (403, 405))
        self.assertTrue(Fundraise.objects.filter(pk=self.draft.pk).exists())
        self.assertTrue(Donation.objects.filter(fundraise=self.draft).exists())

    def test_api_requires_distribution_consent_to_create(self):
        nobody = make_user('api_no_consent')
        response = self.client.post(
            '/api/fundraises/',
            data=json.dumps({
                'title': 'Сбор', 'description': LONG_DESCRIPTION,
                'category': 'other', 'target_amount': '1000',
            }),
            content_type='application/json', **self.auth(nobody),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Fundraise.objects.filter(author=nobody).exists())

    def test_api_creates_draft_not_published(self):
        author = make_user('api_author2')
        give_distribution_consent(author)
        self.client.post(
            '/api/fundraises/',
            data=json.dumps({
                'title': 'Новый через API', 'description': LONG_DESCRIPTION,
                'category': 'other', 'target_amount': '1000',
            }),
            content_type='application/json', **self.auth(author),
        )
        created = Fundraise.objects.get(title='Новый через API')
        self.assertEqual(created.moderation_status, 'draft')
        self.assertFalse(created.accepts_donations)

    def test_api_blocks_second_unfinished_fundraise(self):
        response = self.client.post(
            '/api/fundraises/',
            data=json.dumps({
                'title': 'Второй', 'description': LONG_DESCRIPTION,
                'category': 'other', 'target_amount': '1000',
            }),
            content_type='application/json', **self.auth(self.author),
        )
        self.assertEqual(response.status_code, 400)


class FundraiseStateMachineTests(BaseCase):
    """
    Комбинации status × moderation_status, в которых сбор оживал или
    запирался вместе с деньгами.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('state_author')
        self.donor = make_user('state_donor', balance='1000')
        self.moderator = User.objects.create_user(
            username='state_moderator', email='sm@example.com',
            password=PASSWORD, is_staff=True,
        )
        give_distribution_consent(self.author)
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )

    def test_cancelled_fundraise_cannot_be_resubmitted(self):
        self.fundraise.status = 'cancelled'
        self.fundraise.save(update_fields=['status'])
        self.assertFalse(self.fundraise.submit_for_moderation())

    def test_approval_does_not_resurrect_cancelled_fundraise(self):
        """Одобрение заявки, поданной до отмены, возвращало сбор на витрину."""
        self.fundraise.moderation_status = 'pending'
        self.fundraise.save(update_fields=['moderation_status'])
        self.fundraise.status = 'cancelled'
        self.fundraise.save(update_fields=['status'])

        self.fundraise.approve(self.moderator)
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.status, 'cancelled')
        self.assertFalse(self.fundraise.accepts_donations)

    def test_moderation_page_refuses_decision_on_closed_fundraise(self):
        self.fundraise.moderation_status = 'pending'
        self.fundraise.status = 'cancelled'
        self.fundraise.save(update_fields=['moderation_status', 'status'])

        self.client.login(username='state_moderator', password=PASSWORD)
        self.client.post(reverse('main:moderate_fundraise', args=[self.fundraise.pk]),
                         {'action': 'approve', 'comment': ''})
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.moderation_status, 'pending')

    def test_edited_fundraise_with_donations_can_still_be_cancelled(self):
        """
        Раньше правка переводила сбор в 'draft', и отменить его — то есть
        вернуть деньги — становилось невозможно.
        """
        services.donate(self.donor, self.fundraise, Decimal('300'))
        donor_before = Balance.objects.get(user=self.donor).amount

        self.fundraise.reset_moderation()
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.status, 'active')
        self.assertFalse(self.fundraise.accepts_donations)

        self.client.login(username='state_author', password=PASSWORD)
        self.client.post(reverse('main:cancel_fundraise', args=[self.fundraise.pk]))
        self.assertGreater(Balance.objects.get(user=self.donor).amount, donor_before)

    def test_donor_still_sees_fundraise_taken_off_publication(self):
        services.donate(self.donor, self.fundraise, Decimal('100'))
        self.fundraise.reset_moderation()

        self.client.login(username='state_donor', password=PASSWORD)
        response = self.client.get(reverse('main:fundraise_detail', args=[self.fundraise.pk]))
        self.assertEqual(response.status_code, 200)

    def test_refund_clears_collected_amount(self):
        """Отменённый сбор показывал «собрано 300 ₽» после полного возврата."""
        services.donate(self.donor, self.fundraise, Decimal('300'))
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.current_amount, Decimal('300'))

        services.refund_donations(self.fundraise)
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.current_amount, Decimal('0.00'))
        self.assertEqual(self.fundraise.donors_count, 0)

    def test_restricted_author_fundraise_hidden_by_direct_link(self):
        restrictions_service.apply_restriction(
            user=self.author, kind='suspended', ground='fraud_suspicion',
            reason='Проверка обстоятельств сбора.',
        )
        stranger = make_user('state_stranger')
        self.client.login(username='state_stranger', password=PASSWORD)
        response = self.client.get(reverse('main:fundraise_detail', args=[self.fundraise.pk]))
        self.assertEqual(response.status_code, 404)


class AccountBlockTests(BaseCase):
    """
    Вид ограничения «учётная запись заблокирована» ничем не отличался
    от приостановления операций: заблокированный пользователь свободно
    пользовался сервисом.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('blocked_user', balance='500')
        give_distribution_consent(self.user)
        restrictions_service.apply_restriction(
            user=self.user, kind='blocked', ground='age_restriction',
            reason='Учётная запись принадлежит лицу младше 18 лет.',
        )
        self.client.login(username='blocked_user', password=PASSWORD)

    def test_blocked_user_is_redirected_from_service_pages(self):
        response = self.client.get(reverse('main:dashboard'))
        self.assertRedirects(response, reverse('main:my_restrictions'))

    def test_blocked_user_can_reach_appeal_page(self):
        self.assertEqual(self.client.get(reverse('main:my_restrictions')).status_code, 200)

    def test_blocked_user_keeps_access_to_rights(self):
        """Отзыв согласия и выгрузка данных — права по 152-ФЗ, их не отнять."""
        self.assertEqual(self.client.get(reverse('main:my_consents')).status_code, 200)
        self.assertEqual(self.client.get(reverse('main:privacy_policy')).status_code, 200)

    def test_blocked_user_cannot_create_fundraise(self):
        self.client.post(reverse('main:create_fundraise'), {
            'title': 'Сбор заблокированного', 'description': LONG_DESCRIPTION,
            'category': 'other', 'target_amount': '1000',
        })
        self.assertFalse(Fundraise.objects.filter(title='Сбор заблокированного').exists())

    def test_api_answers_with_code_not_redirect(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = RefreshToken.for_user(self.user).access_token
        response = self.client.get('/api/fundraises/', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 403)


class RestrictionDecisionTests(BaseCase):
    """Решение по возражению выносится по поданному возражению и один раз."""

    def setUp(self):
        super().setUp()
        self.user = make_user('decision_user')
        self.staff = User.objects.create_user(
            username='decision_staff', email='ds@example.com', password=PASSWORD, is_staff=True,
        )
        self.restriction = restrictions_service.apply_restriction(
            user=self.user, kind='suspended', ground='terms_violation',
            reason='Нарушение раздела 8.', actor=self.staff,
        )
        self.client.login(username='decision_staff', password=PASSWORD)

    def test_decision_without_appeal_is_refused(self):
        self.client.post(reverse('main:resolve_restriction', args=[self.restriction.pk]),
                         {'decision': 'upheld', 'comment': 'Оставлено в силе'})
        self.restriction.refresh_from_db()
        self.assertEqual(self.restriction.decision, '')

    def test_second_decision_is_refused(self):
        self.restriction.submit_appeal('Объяснения пользователя по существу дела.')
        self.client.post(reverse('main:resolve_restriction', args=[self.restriction.pk]),
                         {'decision': 'upheld', 'comment': 'Первое решение'})
        self.client.post(reverse('main:resolve_restriction', args=[self.restriction.pk]),
                         {'decision': 'lifted', 'comment': 'Второе решение'})
        self.restriction.refresh_from_db()
        self.assertEqual(self.restriction.decision, 'upheld')
        self.assertIsNone(self.restriction.lifted_at)


class FundraiseDocumentRetentionTests(BaseCase):
    """
    Политика обещает, что документы к сбору удаляются после его закрытия.
    Сбор при закрытии не удаляется, поэтому без отдельного шага медицинские
    справки лежали бы в хранилище бессрочно.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('doc_owner')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='medical',
            target_amount=Decimal('1000'), author=self.user,
            status='completed', moderation_status='approved',
        )
        self.document = FundraiseDocument.objects.create(
            fundraise=self.fundraise, document_type='medical',
            file=SimpleUploadedFile('spravka.pdf', b'%PDF-1.4 test', 'application/pdf'),
        )

    def test_closing_a_fundraise_records_the_moment(self):
        """Без отметки о закрытии срок хранения не от чего отсчитывать."""
        self.fundraise.refresh_from_db()
        self.assertIsNotNone(self.fundraise.closed_at)

    def test_documents_of_closed_fundraise_are_purged(self):
        long_ago = timezone.now() - timezone.timedelta(days=400)
        Fundraise.objects.filter(pk=self.fundraise.pk).update(closed_at=long_ago)

        call_command('enforce_retention', stdout=StringIO())
        self.assertFalse(FundraiseDocument.objects.filter(pk=self.document.pk).exists())

    def test_documents_of_recently_closed_fundraise_are_kept(self):
        """Срок считается от закрытия сбора, а не от загрузки документа."""
        long_ago = timezone.now() - timezone.timedelta(days=400)
        FundraiseDocument.objects.filter(pk=self.document.pk).update(uploaded_at=long_ago)

        call_command('enforce_retention', stdout=StringIO())
        self.assertTrue(FundraiseDocument.objects.filter(pk=self.document.pk).exists())

    def test_documents_of_abandoned_draft_are_purged(self):
        draft = Fundraise.objects.create(
            title='Брошенный', description=LONG_DESCRIPTION, category='medical',
            target_amount=Decimal('1000'), author=make_user('abandoned_author'),
            status='draft', moderation_status='draft',
        )
        document = FundraiseDocument.objects.create(
            fundraise=draft, document_type='medical',
            file=SimpleUploadedFile('old.pdf', b'%PDF-1.4', 'application/pdf'),
        )
        FundraiseDocument.objects.filter(pk=document.pk).update(
            uploaded_at=timezone.now() - timezone.timedelta(days=400),
        )

        call_command('enforce_retention', stdout=StringIO())
        self.assertFalse(FundraiseDocument.objects.filter(pk=document.pk).exists())

    def test_account_deletion_destroys_documents(self):
        self.client.login(username='doc_owner', password=PASSWORD)
        self.client.post(reverse('main:delete_account'), {'confirm': 'doc_owner'})
        self.assertFalse(FundraiseDocument.objects.filter(pk=self.document.pk).exists())


class RepeatedRefundTests(BaseCase):
    """
    Повторный возврат после погашения долга автором отдавал донору сумму
    заново: уже возвращённое не вычиталось. Сумма балансов при этом
    сходилась, но деньги перетекали от автора к донору сверх обязательства.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('repeat_author')
        self.donor = make_user('repeat_donor', balance='1000')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('3'))
    def test_second_refund_returns_only_the_remainder(self):
        services.donate(self.donor, self.fundraise, Decimal('1000'))
        # Автор потратил почти всё: вернуть можно только остаток
        Balance.objects.filter(user=self.author).update(amount=Decimal('100'))

        first = services.refund_donations(self.fundraise)
        self.assertGreater(first['shortfall'], Decimal('0'))
        after_first = Balance.objects.get(user=self.donor).amount

        # Автор погасил долг и возврат повторили
        Balance.objects.filter(user=self.author).update(amount=Decimal('1000'))
        second = services.refund_donations(self.fundraise)

        donor_total = Balance.objects.get(user=self.donor).amount
        # Донору вернулось ровно пожертвованное, не больше
        self.assertEqual(donor_total, Decimal('1000.00'))
        self.assertEqual(first['refunded'] + second['refunded'], Decimal('1000.00'))
        self.assertEqual(second['shortfall'], Decimal('0.00'))
        self.assertGreater(donor_total, after_first)

    def test_third_refund_moves_nothing(self):
        services.donate(self.donor, self.fundraise, Decimal('200'))
        services.refund_donations(self.fundraise)
        before = total_money()
        donor_before = Balance.objects.get(user=self.donor).amount

        services.refund_donations(self.fundraise)
        self.assertEqual(total_money(), before)
        self.assertEqual(Balance.objects.get(user=self.donor).amount, donor_before)

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('0'))
    def test_partial_refund_reduces_collected_amount(self):
        services.donate(self.donor, self.fundraise, Decimal('1000'))
        Balance.objects.filter(user=self.author).update(amount=Decimal('300'))

        result = services.refund_donations(self.fundraise)
        self.fundraise.refresh_from_db()
        # Вернули 300 из 1000 — столько и должно уйти из «собрано»
        self.assertEqual(result['refunded'], Decimal('300.00'))
        self.assertEqual(self.fundraise.current_amount, Decimal('700.00'))
        # Жертвователь, которому вернули не всё, из сбора не ушёл
        self.assertEqual(self.fundraise.donors_count, 1)


class ModerationStateTrapTests(BaseCase):
    """
    Возврат на доработку переводил сбор в 'draft', и сбор с пожертвованиями
    становилось нельзя ни отменить, ни завершить: деньги запирались у автора.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('trap_author')
        self.donor = make_user('trap_donor', balance='1000')
        self.moderator = User.objects.create_user(
            username='trap_moderator', email='tm@example.com',
            password=PASSWORD, is_staff=True,
        )
        give_distribution_consent(self.author)
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )

    def test_request_changes_keeps_refund_possible(self):
        services.donate(self.donor, self.fundraise, Decimal('400'))
        donor_before = Balance.objects.get(user=self.donor).amount

        self.fundraise.request_changes(self.moderator, 'Уточните цель сбора.')
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.status, 'active')
        self.assertFalse(self.fundraise.accepts_donations)

        self.client.login(username='trap_author', password=PASSWORD)
        self.client.post(reverse('main:cancel_fundraise', args=[self.fundraise.pk]))
        self.assertGreater(Balance.objects.get(user=self.donor).amount, donor_before)

    def test_unapproved_fundraise_cannot_be_completed(self):
        """
        Завершение закрывало сбор для модерации: отклонить его с возвратом
        денег было бы уже нельзя.
        """
        services.donate(self.donor, self.fundraise, Decimal('200'))
        self.fundraise.reset_moderation()

        self.client.login(username='trap_author', password=PASSWORD)
        self.client.post(reverse('main:complete_fundraise', args=[self.fundraise.pk]))
        self.fundraise.refresh_from_db()
        self.assertNotEqual(self.fundraise.status, 'completed')

    def test_blocked_user_can_still_delete_account(self):
        """Право на удаление (ст. 14 152-ФЗ, п. 12.1 оферты) блокировкой не отнимается."""
        user = make_user('blocked_deleter')
        restrictions_service.apply_restriction(
            user=user, kind='blocked', ground='terms_violation',
            reason='Нарушение раздела 8.',
        )
        self.client.login(username='blocked_deleter', password=PASSWORD)
        self.client.post(reverse('main:delete_account'), {'confirm': 'blocked_deleter'})

        user.refresh_from_db()
        self.assertTrue(user.username.startswith('deleted_'))


# ==================== РАБОТОСПОСОБНОСТЬ ИНТЕРФЕЙСА ====================

class ConsentGrantingTests(BaseCase):
    """
    Согласие на распространение нельзя было выдать: вью умела его принимать,
    но кнопки в интерфейсе не существовало. Пользователь без этого согласия
    не мог ни создать сбор, ни получить согласие — тупик.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('consent_user')
        self.client.login(username='consent_user', password=PASSWORD)

    def test_page_offers_missing_consent(self):
        body = self.client.get(reverse('main:my_consents')).content.decode()
        self.assertIn('Доступные согласия', body)
        self.assertIn('distribution', body)

    def test_user_can_grant_distribution_consent(self):
        self.client.post(reverse('main:my_consents'), {
            'action': 'reaccept', 'consent_type': 'distribution',
        })
        self.assertTrue(UserConsent.has_active_consent(self.user, 'distribution'))

    def test_after_granting_fundraise_creation_opens(self):
        self.client.post(reverse('main:my_consents'), {
            'action': 'reaccept', 'consent_type': 'distribution',
        })
        response = self.client.get(reverse('main:create_fundraise'))
        self.assertEqual(response.status_code, 200)

    def test_outdated_consent_is_offered_again(self):
        """После обновления редакции согласие надо переподписать."""
        UserConsent.objects.create(
            user=self.user, consent_type='distribution', version='0.9', is_accepted=True,
        )
        body = self.client.get(reverse('main:my_consents')).content.decode()
        self.assertIn('документ обновился', body)


class PasswordRecoveryTests(BaseCase):
    """
    Восстановления пароля не было вовсе: форма регистрации требует email
    «для восстановления доступа», а забывший пароль терял учётную запись
    вместе с остатком на балансе.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('forgetful')

    def test_reset_email_is_sent(self):
        self.client.post(reverse('main:password_reset'), {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('password-reset', mail.outbox[0].body)

    def test_unknown_email_gets_the_same_answer(self):
        """Иначе форма превращается в проверку «зарегистрирован ли адрес»."""
        known = self.client.post(reverse('main:password_reset'), {'email': self.user.email})
        unknown = self.client.post(reverse('main:password_reset'), {'email': 'nobody@example.com'})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known['Location'], unknown['Location'])
        self.assertEqual(len(mail.outbox), 1)

    def test_link_from_email_sets_new_password(self):
        self.client.post(reverse('main:password_reset'), {'email': self.user.email})
        body = mail.outbox[0].body
        link = [word for word in body.split() if '/password-reset/' in word and word.count('/') > 3][0]
        path = link[link.index('/password-reset/'):]

        # Django подменяет токен в URL на служебный и редиректит на форму
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(response.redirect_chain[-1][0] if response.redirect_chain
                                    else path,
                                    {'new_password1': 'Meadow-Lantern-77',
                                     'new_password2': 'Meadow-Lantern-77'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Meadow-Lantern-77'))

    def test_password_change_requires_login_and_is_logged(self):
        self.assertEqual(
            self.client.get(reverse('main:password_change')).status_code, 302,
        )
        self.client.login(username='forgetful', password=PASSWORD)
        self.client.post(reverse('main:password_change'), {
            'old_password': PASSWORD,
            'new_password1': 'Harbor-Meadow-88',
            'new_password2': 'Harbor-Meadow-88',
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Harbor-Meadow-88'))
        self.assertTrue(
            SecurityLog.objects.filter(user=self.user, action='password_change').exists()
        )


class AdminPagesTests(BaseCase):
    """
    Две страницы админки падали с 500, и ни одна не была покрыта тестом:
    добавление ограничения и добавление сбора.
    """

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('pages_admin', 'pa@example.com', PASSWORD)
        self.client.login(username='pages_admin', password=PASSWORD)

    def test_admin_pages_render(self):
        for path in [
            '/admin/', '/admin/main/fundraise/', '/admin/main/fundraise/add/',
            '/admin/main/accountrestriction/', '/admin/main/accountrestriction/add/',
            '/admin/main/databreachincident/add/', '/admin/main/personaldataaccesslog/',
            '/admin/main/fundraisedocument/', '/admin/main/commissiontransaction/',
            '/admin/main/withdrawalrequest/', '/admin/main/userverification/',
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_restriction_is_created_through_admin_form(self):
        """
        Форма создавала ограничение в обход save() модели: объект оставался
        без created_at, Django падал на записи в журнал действий, всё
        откатывалось — а письмо пользователю уже уходило.
        """
        subject = make_user('restricted_by_admin')
        response = self.client.post('/admin/main/accountrestriction/add/', {
            'user': subject.pk,
            'kind': 'suspended',
            'ground': 'fraud_suspicion',
            'reason': 'Признаки использования чужой карты.',
            'internal_note': '',
        })
        self.assertIn(response.status_code, (200, 302))
        restriction = AccountRestriction.objects.filter(user=subject).first()
        self.assertIsNotNone(restriction)
        self.assertIsNotNone(restriction.created_at)
        self.assertEqual(restriction.created_by, self.admin)


class WithdrawalCancelTests(BaseCase):
    """Кнопка отмены заявки всегда возвращала 403: CSRF-токена на странице не было."""

    def setUp(self):
        super().setUp()
        self.user = make_user('cancel_user', balance='5000')
        self.client.login(username='cancel_user', password=PASSWORD)
        self.withdrawal = services.hold_for_withdrawal(
            self.user, Decimal('1000'), 'card', {'card_number': '4111111111111111'}, '**** 1111',
        )

    def test_page_contains_working_form(self):
        body = self.client.get(reverse('main:my_withdrawals')).content.decode()
        self.assertIn('csrfmiddlewaretoken', body)
        self.assertIn(reverse('main:cancel_withdrawal', args=[self.withdrawal.pk]), body)

    def test_post_cancels_and_returns_money(self):
        before = Balance.objects.get(user=self.user).amount
        self.client.post(reverse('main:cancel_withdrawal', args=[self.withdrawal.pk]))
        self.withdrawal.refresh_from_db()
        self.assertEqual(self.withdrawal.status, 'cancelled')
        self.assertEqual(Balance.objects.get(user=self.user).amount, before + Decimal('1000'))

    def test_get_is_refused(self):
        self.assertEqual(
            self.client.get(reverse('main:cancel_withdrawal', args=[self.withdrawal.pk])).status_code,
            405,
        )


class VerificationInputTests(BaseCase):
    """Пустая дата рождения роняла страницу верификации с 500."""

    def setUp(self):
        super().setUp()
        self.user = make_user('verify_user')
        self.client.login(username='verify_user', password=PASSWORD)

    def post(self, birth_date):
        return self.client.post(reverse('main:verification'), {
            'full_name': 'Иванов Иван Иванович',
            'birth_date': birth_date,
            'passport_series': '1234',
            'passport_number': '567890',
        }, follow=True)

    def test_empty_birth_date_does_not_crash(self):
        response = self.post('')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            UserVerification.objects.filter(user=self.user, submitted_at__isnull=False).exists()
        )

    def test_minor_is_refused(self):
        """Оферта ограничивает сервис совершеннолетними, но проверки не было."""
        recent = timezone.localdate() - timezone.timedelta(days=365 * 15)
        response = self.post(recent.strftime('%Y-%m-%d'))
        self.assertIn('18 лет', response.content.decode())

    def test_adult_is_accepted(self):
        adult = timezone.localdate() - timezone.timedelta(days=365 * 30)
        self.post(adult.strftime('%Y-%m-%d'))
        verification = UserVerification.objects.get(user=self.user)
        self.assertIsNotNone(verification.submitted_at)
        self.assertEqual(verification.birth_date, adult)


class FundraiseExpiryTests(BaseCase):
    """
    Дата окончания показывалась на странице сбора, но не проверялась нигде:
    сбор с истёкшим сроком продолжал принимать пожертвования.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('expiry_author')
        self.donor = make_user('expiry_donor', balance='1000')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
            end_date=timezone.now() - timezone.timedelta(days=1),
        )

    def test_expired_fundraise_refuses_donation(self):
        with self.assertRaises(services.OperationRejected):
            services.donate(self.donor, self.fundraise, Decimal('100'))

    def test_command_closes_expired(self):
        call_command('close_expired_fundraises', stdout=StringIO())
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.status, 'completed')
        self.assertIsNotNone(self.fundraise.closed_at)

    def test_future_end_date_does_not_block(self):
        self.fundraise.end_date = timezone.now() + timezone.timedelta(days=5)
        self.fundraise.save(update_fields=['end_date'])
        services.donate(self.donor, self.fundraise, Decimal('100'))
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.current_amount, Decimal('100'))


class ApiFundraiseFlowTests(BaseCase):
    """Через API сбор нельзя было отправить на проверку — он оставался черновиком навсегда."""

    def setUp(self):
        super().setUp()
        self.author = make_user('api_flow_author')
        give_distribution_consent(self.author)
        from rest_framework_simplejwt.tokens import RefreshToken
        self.headers = {
            'HTTP_AUTHORIZATION': f'Bearer {RefreshToken.for_user(self.author).access_token}',
        }

    def test_create_returns_id_and_moderation_status(self):
        response = self.client.post(
            '/api/fundraises/',
            data=json.dumps({
                'title': 'Сбор через API', 'description': LONG_DESCRIPTION,
                'category': 'other', 'target_amount': '1000',
            }),
            content_type='application/json', **self.headers,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn('id', body)
        self.assertEqual(body['moderation_status'], 'draft')

    def test_submit_moves_to_pending(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('1000'), author=self.author,
            status='draft', moderation_status='draft',
        )
        response = self.client.post(f'/api/fundraises/{fundraise.pk}/submit/', **self.headers)
        self.assertEqual(response.status_code, 200)
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.moderation_status, 'pending')

    def test_submit_reports_problems(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description='коротко', category='medical',
            target_amount=Decimal('1000'), author=self.author,
            status='draft', moderation_status='draft',
        )
        response = self.client.post(f'/api/fundraises/{fundraise.pk}/submit/', **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()['problems'])


class TwoFactorDisableTests(BaseCase):
    """Включить 2FA было можно, выключить — нет."""

    def setUp(self):
        super().setUp()
        self.user = make_user('tfa_user')
        self.two_factor = TwoFactorAuth.objects.create(
            user=self.user,
            secret_key=TwoFactorAuthService.generate_secret(),
            is_enabled=True,
            backup_codes=TwoFactorAuthService.generate_backup_codes(),
        )
        self.client.login(username='tfa_user', password=PASSWORD)

    def test_wrong_code_keeps_protection(self):
        self.client.post(reverse('main:disable_2fa'), {'code': '000000'})
        self.two_factor.refresh_from_db()
        self.assertTrue(self.two_factor.is_enabled)

    def test_backup_code_disables(self):
        code = self.two_factor.backup_codes[0]
        self.client.post(reverse('main:disable_2fa'), {'code': code})
        self.two_factor.refresh_from_db()
        self.assertFalse(self.two_factor.is_enabled)
        self.assertEqual(self.two_factor.backup_codes, [])


class InterfacePagesTests(BaseCase):
    """
    Рендер основных страниц. Раньше в тестах не было ни одной из них,
    и ошибка в шаблоне обнаруживалась только пользователем.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('pages_user', balance='5000')
        self.client.login(username='pages_user', password=PASSWORD)

    def test_pages_render(self):
        for name in [
            'main:dashboard', 'main:transfer', 'main:history', 'main:topup',
            'main:profile', 'main:leaders', 'main:fundraises',
            'main:my_fundraises', 'main:my_donations', 'main:my_consents',
            'main:withdrawal', 'main:my_withdrawals', 'main:setup_2fa',
            'main:verification', 'main:my_restrictions', 'main:privacy_policy',
            'main:user_agreement', 'main:cookie_policy', 'main:consent_processing',
            'main:consent_distribution', 'main:legal_details', 'main:password_change',
        ]:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_quick_help_redirects_when_nobody_to_help(self):
        """Страница осмысленна только при наличии получателей — иначе перенаправляет."""
        self.assertEqual(self.client.get(reverse('main:quick_help')).status_code, 302)

    def test_anonymous_pages_render(self):
        self.client.logout()
        for name in ['main:login', 'main:register', 'main:password_reset']:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_topup_does_not_promise_free_money_in_live_mode(self):
        """
        Шаблон ссылался на переменную, которой не было в контексте: в боевом
        режиме страница уверяла, что деньги не списываются.
        """
        with override_settings(ALLOW_SIMULATED_TOPUP=False, USE_REAL_PAYMENTS=True):
            body = self.client.get(reverse('main:topup')).content.decode()
        self.assertNotIn('ТЕСТОВЫЙ РЕЖИМ', body)
        self.assertIn('ЮKassa', body)


# ==================== ПОДТВЕРЖДЕНИЕ АДРЕСА ПОЧТЫ ====================

class EmailConfirmationTests(BaseCase):
    """
    Адрес не проверялся вовсе: на непроверенный адрес уходили письма
    о блокировке счёта и ссылка восстановления доступа, а опечатка
    означала, что вернуть себе доступ к деньгам невозможно.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user('unconfirmed_user', balance='5000', email_confirmed=False)

    def test_registration_sends_confirmation(self):
        self.client.post(reverse('main:register'), {
            'username': 'fresh_user', 'email': 'fresh@example.com',
            'password1': 'Sunrise-Harbor-42', 'password2': 'Sunrise-Harbor-42',
            'accept_terms': 'on', 'consent_data_processing': 'on',
        })
        user = User.objects.get(username='fresh_user')
        self.assertFalse(EmailConfirmation.is_email_confirmed(user))
        self.assertTrue(any('email/confirm' in message.body for message in mail.outbox))

    def test_withdrawal_blocked_until_confirmed(self):
        with self.assertRaises(services.OperationRejected):
            services.hold_for_withdrawal(
                self.user, Decimal('1000'), 'card', {'card_number': '4111111111111111'}, '**** 1111',
            )
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('5000'))

    def test_publication_blocked_until_confirmed(self):
        give_distribution_consent(self.user)
        fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('1000'), author=self.user,
            status='draft', moderation_status='draft',
        )
        problems = moderation.check_can_submit(fundraise)
        self.assertTrue(any('электронной почты' in problem for problem in problems))

    def test_donation_still_works(self):
        """
        Ограничивается распоряжение деньгами в свою пользу — вывод, перевод
        и публикация сбора. Пожертвование остаётся доступным: деньги уходят
        в проверенный сбор, а не обратно жертвователю, и обойти через него
        запрет нельзя.
        """
        author = make_user('confirmed_author')
        fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=author,
            status='active', moderation_status='approved',
        )
        services.donate(self.user, fundraise, Decimal('100'))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.current_amount, Decimal('100'))

    def test_link_confirms_address(self):
        email_confirmation.send_confirmation(self.user)
        link = [
            word for word in mail.outbox[-1].body.split() if '/email/confirm/' in word
        ][0]
        path = link[link.index('/email/confirm/'):]

        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailConfirmation.is_email_confirmed(self.user))

    def test_link_stops_working_after_email_change(self):
        """Ссылка, ушедшая на старый адрес, не должна подтверждать новый."""
        email_confirmation.send_confirmation(self.user)
        link = [w for w in mail.outbox[-1].body.split() if '/email/confirm/' in w][0]
        path = link[link.index('/email/confirm/'):]

        self.user.email = 'another@example.com'
        self.user.save(update_fields=['email'])

        self.client.get(path)
        self.assertFalse(EmailConfirmation.is_email_confirmed(self.user))

    def test_changing_email_invalidates_confirmation(self):
        confirmed = make_user('changes_email')
        self.assertTrue(EmailConfirmation.is_email_confirmed(confirmed))
        confirmed.email = 'new-address@example.com'
        confirmed.save(update_fields=['email'])
        self.assertFalse(EmailConfirmation.is_email_confirmed(confirmed))

    def test_resend_is_rate_limited(self):
        self.client.login(username='unconfirmed_user', password=PASSWORD)
        self.client.post(reverse('main:resend_email_confirmation'))
        sent_after_first = len(mail.outbox)
        self.client.post(reverse('main:resend_email_confirmation'))
        self.assertEqual(len(mail.outbox), sent_after_first)

    def test_existing_accounts_are_not_locked_out(self):
        """Старые учётные записи не лишаются вывода задним числом."""
        legacy = make_user('legacy_user', balance='5000', email_confirmed=False)
        confirmation = EmailConfirmation.for_user(legacy)
        confirmation.is_legacy = True
        confirmation.save(update_fields=['is_legacy'])

        withdrawal = services.hold_for_withdrawal(
            legacy, Decimal('1000'), 'card', {'card_number': '4111111111111111'}, '**** 1111',
        )
        self.assertIsNotNone(withdrawal.pk)


class EmailDomainTests(BaseCase):
    """Одноразовые адреса и опечатки дают адрес, до которого не достучаться."""

    def register(self, email):
        return self.client.post(reverse('main:register'), {
            'username': 'domain_probe', 'email': email,
            'password1': 'Sunrise-Harbor-42', 'password2': 'Sunrise-Harbor-42',
            'accept_terms': 'on', 'consent_data_processing': 'on',
        })

    def test_disposable_domain_refused(self):
        self.register('someone@mailinator.com')
        self.assertFalse(User.objects.filter(username='domain_probe').exists())

    def test_typo_is_caught_including_swapped_letters(self):
        from main.utils.email_domains import check_email_domain

        self.assertIn('gmail.com', check_email_domain('a@gmial.com'))
        self.assertIn('yandex.ru', check_email_domain('a@yadnex.ru'))

    def test_normal_addresses_pass(self):
        from main.utils.email_domains import check_email_domain

        for address in ['a@gmail.com', 'a@mail.ru', 'a@ya.ru',
                        'a@sberbank.ru', 'a@своя-компания.рф']:
            with self.subTest(address=address):
                self.assertIsNone(check_email_domain(address))

    def test_valid_registration_passes(self):
        self.register('someone@yandex.ru')
        self.assertTrue(User.objects.filter(username='domain_probe').exists())


class DebtRepaymentTests(BaseCase):
    """
    Пункт 7.4 оферты обещает погашение задолженности перед жертвователями,
    но увидеть долг и вернуть деньги автор не мог никак.
    """

    def setUp(self):
        super().setUp()
        self.author = make_user('debt_author')
        self.donor = make_user('debt_donor', balance='1000')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )
        self.client.login(username='debt_author', password=PASSWORD)

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('0'))
    def create_debt(self):
        services.donate(self.donor, self.fundraise, Decimal('1000'))
        # Автор потратил почти всё: при отмене вернуть можно только остаток
        Balance.objects.filter(user=self.author).update(amount=Decimal('200'))
        services.refund_donations(self.fundraise)
        self.fundraise.status = 'cancelled'
        self.fundraise.save(update_fields=['status'])

    def test_debt_is_visible(self):
        self.create_debt()
        debt = services.outstanding_debt(self.author)
        self.assertEqual(debt['total'], Decimal('800.00'))

        body = self.client.get(reverse('main:my_debt')).content.decode()
        self.assertIn('800', body)

    def test_repayment_returns_money_to_donor(self):
        self.create_debt()
        donor_before = Balance.objects.get(user=self.donor).amount
        Balance.objects.filter(user=self.author).update(amount=Decimal('800'))

        self.client.post(reverse('main:my_debt'))

        self.assertEqual(
            Balance.objects.get(user=self.donor).amount, donor_before + Decimal('800'),
        )
        self.assertEqual(services.outstanding_debt(self.author)['total'], Decimal('0.00'))

    def test_partial_repayment_keeps_remainder(self):
        self.create_debt()
        Balance.objects.filter(user=self.author).update(amount=Decimal('300'))
        self.client.post(reverse('main:my_debt'))
        self.assertEqual(services.outstanding_debt(self.author)['total'], Decimal('500.00'))

    def test_repayment_does_not_create_money(self):
        self.create_debt()
        Balance.objects.filter(user=self.author).update(amount=Decimal('800'))
        before = total_money()
        self.client.post(reverse('main:my_debt'))
        self.assertEqual(total_money(), before)

    def test_no_debt_page_says_so(self):
        body = self.client.get(reverse('main:my_debt')).content.decode()
        self.assertIn('Задолженности нет', body)


class DonorCountTests(BaseCase):
    """Счётчик показывал пожертвования, а не людей."""

    def setUp(self):
        super().setUp()
        self.author = make_user('count_author')
        self.donor = make_user('count_donor', balance='1000')
        self.fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )

    def test_same_donor_counted_once(self):
        services.donate(self.donor, self.fundraise, Decimal('100'))
        services.donate(self.donor, self.fundraise, Decimal('100'))
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.donors_count, 1)
        self.assertEqual(self.fundraise.current_amount, Decimal('200'))

    def test_different_donors_counted_separately(self):
        other = make_user('count_donor2', balance='1000')
        services.donate(self.donor, self.fundraise, Decimal('100'))
        services.donate(other, self.fundraise, Decimal('100'))
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.donors_count, 2)

    def test_refund_removes_donor_from_count(self):
        services.donate(self.donor, self.fundraise, Decimal('100'))
        services.refund_donations(self.fundraise)
        self.fundraise.refresh_from_db()
        self.assertEqual(self.fundraise.donors_count, 0)


class RemovedEndpointsTests(BaseCase):
    """Мёртвые адреса убраны, а не оставлены «на всякий случай»."""

    def test_dead_urls_are_gone(self):
        from django.urls import NoReverseMatch

        for name in ['main:get_balance_json', 'main:cancel_transaction']:
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)


class ChecksAfterReviewTests(BaseCase):
    """Дефекты, найденные проверкой последней волны изменений."""

    def setUp(self):
        super().setUp()
        self.author = make_user('review_author')
        self.donor = make_user('review_donor', balance='1000')

    def test_real_domains_similar_to_popular_are_accepted(self):
        """mail.com, ymail.com и email.com — настоящие провайдеры."""
        from main.utils.email_domains import check_email_domain

        for address in ['a@mail.com', 'a@ymail.com', 'a@email.com', 'a@googlemail.com']:
            with self.subTest(address=address):
                self.assertIsNone(check_email_domain(address))

    def test_typo_warning_can_be_overridden_on_second_attempt(self):
        """Иначе проверка опечаток становится запретом на регистрацию."""
        data = {
            'username': 'typo_user', 'email': 'someone@gmial.com',
            'password1': 'Sunrise-Harbor-42', 'password2': 'Sunrise-Harbor-42',
            'accept_terms': 'on', 'consent_data_processing': 'on',
        }
        self.client.post(reverse('main:register'), data)
        self.assertFalse(User.objects.filter(username='typo_user').exists())

        self.client.post(reverse('main:register'), {**data, 'email_typo_confirmed': 'on'})
        self.assertTrue(User.objects.filter(username='typo_user').exists())

    def test_api_registration_checks_domain_and_sends_confirmation(self):
        response = self.client.post(
            '/api/auth/register/',
            data=json.dumps({
                'username': 'api_newbie', 'email': 'someone@mailinator.com',
                'password': 'Sunrise-Harbor-42', 'password2': 'Sunrise-Harbor-42',
                'accept_terms': True, 'consent_data_processing': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/api/auth/register/',
            data=json.dumps({
                'username': 'api_newbie', 'email': 'someone@yandex.ru',
                'password': 'Sunrise-Harbor-42', 'password2': 'Sunrise-Harbor-42',
                'accept_terms': True, 'consent_data_processing': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['email_confirmed'])
        self.assertTrue(any('email/confirm' in message.body for message in mail.outbox))

    @override_settings(DONATION_COMMISSION_PERCENT=Decimal('10'))
    def test_author_does_not_repay_commission_he_never_received(self):
        """
        Если на служебном счёте не хватает, это долг сервиса, а не автора:
        автор получил сумму за вычетом комиссии.
        """
        fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )
        services.donate(self.donor, fundraise, Decimal('1000'))
        # Комиссия выведена со служебного счёта, у автора денег достаточно
        Balance.objects.filter(user=self.service_account).update(amount=Decimal('0'))
        Balance.objects.filter(user=self.author).update(amount=Decimal('5000'))
        author_before = Balance.objects.get(user=self.author).amount

        services.refund_donations(fundraise)

        spent_by_author = author_before - Balance.objects.get(user=self.author).amount
        self.assertEqual(spent_by_author, Decimal('900.00'))

    def test_blocked_user_can_reach_debt_page(self):
        """Иначе круг замыкается: погасить нельзя, а удалить мешает долг."""
        restrictions_service.apply_restriction(
            user=self.author, kind='blocked', ground='terms_violation',
            reason='Нарушение раздела 8.',
        )
        self.client.login(username='review_author', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('main:my_debt')).status_code, 200)
        self.assertEqual(self.client.get(reverse('main:topup')).status_code, 200)

    def test_legacy_record_without_email_is_not_confirmed(self):
        user = make_user('no_email_user', balance='5000', email_confirmed=False)
        confirmation = EmailConfirmation.for_user(user)
        confirmation.is_legacy = True
        confirmation.save(update_fields=['is_legacy'])
        user.email = ''
        user.save(update_fields=['email'])

        self.assertFalse(EmailConfirmation.is_email_confirmed(user))
        with self.assertRaises(services.OperationRejected):
            services.hold_for_withdrawal(
                user, Decimal('1000'), 'card', {'card_number': '4111111111111111'}, '**** 1111',
            )

    def test_legacy_user_can_confirm_voluntarily(self):
        user = make_user('legacy_volunteer', email_confirmed=False)
        confirmation = EmailConfirmation.for_user(user)
        confirmation.is_legacy = True
        confirmation.save(update_fields=['is_legacy'])

        self.client.login(username='legacy_volunteer', password=PASSWORD)
        self.client.post(reverse('main:resend_email_confirmation'))
        self.assertTrue(any('email/confirm' in message.body for message in mail.outbox))

    def test_transfer_requires_confirmed_email(self):
        """Иначе неподтверждённый переводит баланс и выводит его с другого счёта."""
        unconfirmed = make_user('unconfirmed_sender', balance='1000', email_confirmed=False)
        with self.assertRaises(services.OperationRejected):
            services.transfer(unconfirmed, self.author, Decimal('100'))

    def test_donor_returning_after_refund_is_counted_again(self):
        fundraise = Fundraise.objects.create(
            title='Сбор', description=LONG_DESCRIPTION, category='other',
            target_amount=Decimal('10000'), author=self.author,
            status='active', moderation_status='approved',
        )
        services.donate(self.donor, fundraise, Decimal('100'))
        services.refund_donations(fundraise)
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.donors_count, 0)

        # Возврат оставил сбор активным только в этом тесте — важно, что
        # счётчик не уходит в расхождение с фактом
        services.donate(self.donor, fundraise, Decimal('100'))
        fundraise.refresh_from_db()
        self.assertEqual(fundraise.donors_count, 1)


class MobileAppTests(TestCase):
    """
    Приложение: манифест, service worker, офлайн, раздача APK.

    Проверяется не «страница открывается», а то, что ломает приложение
    молча: кэш денежных страниц, внешние адреса в service worker,
    отсутствующие иконки и отпечаток ключа подписи.
    """

    def test_dev_machine_needs_no_production_packages(self):
        """
        При DEBUG=True настройки не должны требовать пакеты из раздела
        «Прод» в requirements.txt.

        Whitenoise стоял в MIDDLEWARE безусловно, а числился
        прод-зависимостью: машина разработчика без него не запускалась
        вовсе — вместо сервера ModuleNotFoundError и стена трассировки.

        Настройки исполняются заново с DEBUG=True в отдельном
        пространстве имён: прогон тестов идёт с DEBUG=False, и проверять
        settings.MIDDLEWARE как есть — значит не проверять ничего.
        """
        import importlib.util
        import os
        from unittest.mock import patch

        source = Path(__file__).resolve().parent.parent / 'angelsheart' / 'settings.py'
        spec = importlib.util.spec_from_file_location('settings_debug_probe', source)
        probe = importlib.util.module_from_spec(spec)

        with patch.dict(os.environ, {'DEBUG': 'True'}, clear=False):
            spec.loader.exec_module(probe)

        self.assertTrue(probe.DEBUG, 'проба не поднялась в режиме разработки')
        for middleware in probe.MIDDLEWARE:
            for package in ('whitenoise', 'psycopg', 'redis', 'gunicorn'):
                self.assertNotIn(
                    package, middleware,
                    f'{middleware} требует прод-пакет {package} на машине разработчика',
                )

    def test_manifest_is_valid_and_self_hosted(self):
        response = self.client.get(reverse('main:web_manifest'))
        self.assertEqual(response.status_code, 200)
        manifest = json.loads(response.content)

        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['start_url'], '/')
        self.assertTrue(manifest['icons'])

        # Иконка 192 и maskable обязательны: без первой Android не
        # предлагает установку, без второй значок обрезается в круг
        # вместе с краями рисунка
        sizes = {icon['sizes'] for icon in manifest['icons']}
        self.assertIn('192x192', sizes)
        purposes = {icon.get('purpose', 'any') for icon in manifest['icons']}
        self.assertIn('maskable', purposes)

        for icon in manifest['icons']:
            self.assertFalse(
                icon['src'].startswith('http'),
                f"иконка {icon['src']} ведёт на внешний адрес",
            )

    def test_manifest_icons_exist(self):
        """Манифест, ссылающийся на несуществующий файл, ломает установку."""
        from django.contrib.staticfiles import finders

        manifest = json.loads(self.client.get(reverse('main:web_manifest')).content)
        for icon in manifest['icons']:
            path = icon['src'].split('/static/', 1)[-1]
            self.assertIsNotNone(
                finders.find(path), f'иконка {path} не найдена в статике',
            )

    def test_service_worker_served_from_root(self):
        """
        Область действия service worker ограничена каталогом, из которого
        он получен: файл из /static/ управлял бы только статикой.
        """
        self.assertEqual(reverse('main:service_worker'), '/service-worker.js')

        response = self.client.get('/service-worker.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response['Content-Type'])
        # Сам файл кэшировать нельзя, иначе обновление не доедет никогда
        self.assertIn('no-store', response['Cache-Control'])

    def test_service_worker_caches_only_static_and_offline(self):
        """
        Единственный надёжный способ проверить кэш — посмотреть, что
        именно service worker кладёт в него при установке. Проверка
        «в файле упоминается /api/» прошла бы и на коде, который эти
        пути никуда не передаёт.
        """
        body = self.client.get('/service-worker.js').content.decode()

        precache = re.search(r'const PRECACHE = \[(.*?)\];', body, re.S)
        self.assertIsNotNone(precache, 'список прекэша не найден')
        urls = re.findall(r"'([^']+)'", precache.group(1))
        self.assertTrue(urls)

        for url in urls:
            self.assertTrue(
                url.startswith('/static/') or url == reverse('main:offline'),
                f'в кэш приложения попадает {url} — это не статика и не офлайн-страница',
            )

        # Прекэш запрашивается без куки: иначе сервер соберёт страницу
        # для текущего пользователя и его баланс осядет в кэше, который
        # переживает выход из учётной записи
        self.assertIn("credentials: 'omit'", body)

        # Навигации никогда не кладутся в кэш: ветка, отвечающая за
        # страницы, не должна ничего писать
        navigation_branch = body.split("request.mode === 'navigate'", 1)[1].split('return;', 1)[0]
        self.assertNotIn('cache.put', navigation_branch)
        self.assertNotIn('caches.open', navigation_branch)

    def test_service_worker_has_no_template_leftovers(self):
        """
        Незакрытый или многострочный {# #} уезжает в файл текстом, и
        service worker перестаёт быть валидным JavaScript — браузер
        молча отказывается его регистрировать, а на сайте всё выглядит
        как обычно. Один раз уже уехал.
        """
        body = self.client.get('/service-worker.js').content.decode()
        for marker in ('{#', '#}', '{%', '{{'):
            self.assertNotIn(marker, body, f'в service worker осталось {marker}')
        self.assertTrue(body.lstrip().startswith('/*'))

    def test_service_worker_sensitive_paths_listed(self):
        """Список запрещённых к кэшу путей покрывает страницы с деньгами."""
        body = self.client.get('/service-worker.js').content.decode()
        for path in ('/api/', '/admin/', '/payment/', '/verification/', '/media/',
                     '/history/', '/transfer/', '/topup/', '/withdrawal/', '/my-'):
            self.assertIn(f"'{path}'", body, f'{path} не исключён из кэша')

    def test_navigation_gets_offline_page_before_any_filter(self):
        """
        Обработчик навигаций обязан стоять ДО отсева «денежных» адресов.

        Проверено вживую (сервер останавливался, браузер шёл на
        /history/): когда отсев был первым, service worker пропускал
        такой адрес мимо себя, и человек, потерявший сеть на «Истории»,
        видел серую ошибку браузера вместо страницы «нет соединения».
        То есть офлайн-режим не работал ровно там, где нужен, и заметить
        это по тексту файла было нельзя — все нужные строки в нём были.
        """
        body = self.client.get('/service-worker.js').content.decode()
        handler = body.split("addEventListener('fetch'", 1)[1]

        navigate_at = handler.index("request.mode === 'navigate'")
        sensitive_at = handler.index('isSensitive(url)')
        self.assertLess(
            navigate_at, sensitive_at,
            'отсев адресов стоит раньше выдачи офлайн-страницы — '
            'на денежных страницах офлайн-режим не сработает',
        )
        self.assertIn('offlineFallback', handler)

    def test_offline_page_reveals_nothing_about_user(self):
        """
        Офлайн-страницу кладёт в кэш service worker, а кэш общий на
        устройство и переживает выход из учётной записи. Значит, она
        обязана быть одинаковой для всех: иначе на телефоне останется
        страница с балансом того, кто входил последним.
        """
        anonymous = self.client.get(reverse('main:offline')).content.decode()

        user = make_user('offline_probe', balance='12345.67')
        self.client.force_login(user)
        authorized = self.client.get(reverse('main:offline')).content.decode()

        self.assertEqual(anonymous, authorized)
        self.assertNotIn('offline_probe', authorized)
        self.assertNotIn('12345', authorized)
        # Шапки base.html здесь быть не должно — именно она рисует баланс
        self.assertNotIn('balance-card-mini', authorized)
        self.assertNotIn('bottom-nav', authorized)

        # Многострочный {# #} Django не понимает — он однострочный, и его
        # содержимое уезжает на страницу как текст. Один раз уже уехало.
        self.assertNotIn('{#', authorized)
        self.assertNotIn('{%', authorized)
        self.assertTrue(authorized.lstrip().startswith('<!DOCTYPE html>'))

    def test_cache_version_changes_when_offline_page_changes(self):
        """
        Если версия не зависит от шаблона, правка офлайн-страницы не
        меняет ни версию, ни байты service worker: браузер не увидит
        обновления и навсегда оставит в кэше старую страницу.
        """
        from main import pwa

        before = pwa._cache_version()

        template = Path(__file__).resolve().parent / 'templates' / 'main' / 'offline.html'
        original = template.read_bytes()
        try:
            template.write_bytes(original + '\n<!-- правка -->\n'.encode('utf-8'))
            self.assertNotEqual(pwa._cache_version(), before)
        finally:
            template.write_bytes(original)

        self.assertEqual(pwa._cache_version(), before)

    def test_service_worker_has_no_external_urls(self):
        """
        Внешний адрес в service worker означал бы обращение к чужому
        серверу на каждой загрузке страницы — ровно то, от чего
        избавлялись, убирая CDN.
        """
        import re

        body = self.client.get('/service-worker.js').content.decode()
        # Ищем `//host`: это ловит и `https://example.com`, и запись
        # без протокола. Упоминание хоста в комментарии не ловится —
        # именно на этом ложно падал такой же тест для вендорного CSS.
        external = re.findall(r'//[a-z0-9.-]+\.[a-z]{2,}', body)
        self.assertEqual(external, [], f'внешние адреса в service worker: {external}')

    def test_offline_page_works(self):
        response = self.client.get(reverse('main:offline'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'соединени')

    def test_base_template_links_manifest_and_worker(self):
        response = self.client.get(reverse('main:login'))
        self.assertContains(response, 'manifest.webmanifest')
        self.assertContains(response, 'service-worker.js')

    def test_asset_links_absent_without_fingerprint(self):
        """
        Неверный отпечаток хуже отсутствующего: приложение молча
        покажет адресную строку.
        """
        with override_settings(ANDROID_SIGNING_FINGERPRINTS='   ,  '):
            # Пробелы и пустые элементы — не отпечаток: их отбрасывание
            # не должно приводить к пустому, но формально валидному файлу
            self.assertEqual(self.client.get('/.well-known/assetlinks.json').status_code, 404)

    def test_asset_links_with_fingerprint(self):
        with override_settings(
            ANDROID_SIGNING_FINGERPRINTS='AA:BB, CC:DD',
            ANDROID_PACKAGE_NAME='ru.test.app',
        ):
            response = self.client.get('/.well-known/assetlinks.json')
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)

        self.assertEqual(data[0]['target']['package_name'], 'ru.test.app')
        self.assertEqual(data[0]['target']['sha256_cert_fingerprints'], ['AA:BB', 'CC:DD'])
        self.assertIn('delegate_permission/common.handle_all_urls', data[0]['relation'])

    def test_app_page_offers_browser_install_without_apk(self):
        with override_settings(ANDROID_APK_PATH='/nonexistent/app.apk'):
            response = self.client.get(reverse('main:app_page'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Установить приложение')
        # Кнопки скачивания быть не должно: ссылка на отсутствующий файл
        # приводит человека на страницу ошибки
        self.assertNotContains(response, 'Скачать APK')

    def test_apk_download_404_without_file(self):
        with override_settings(ANDROID_APK_PATH='/nonexistent/app.apk'):
            self.assertEqual(self.client.get(reverse('main:download_apk')).status_code, 404)

    def test_apk_download_serves_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'angelsheart.apk'
            apk.write_bytes(b'PK\x03\x04fake')
            with override_settings(ANDROID_APK_PATH=str(apk)):
                page = self.client.get(reverse('main:app_page'))
                self.assertContains(page, 'Скачать APK')

                response = self.client.get(reverse('main:download_apk'))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response['Content-Type'], 'application/vnd.android.package-archive',
                )
                self.assertIn('attachment', response['Content-Disposition'])
                self.assertEqual(b''.join(response.streaming_content), b'PK\x03\x04fake')

    def test_mobile_css_is_last_stylesheet(self):
        """
        Вёрстка написана инлайн-стилями; мобильный слой перекрывает её
        только если подключён последним.
        """
        body = self.client.get(reverse('main:login')).content.decode()
        self.assertIn('mobile.css', body)
        self.assertGreater(body.index('mobile.css'), body.index('all.min.css'))
        # Главное: позже встроенного <style> в теле документа. Раньше
        # ссылка стояла в <head>, и правила мобильного слоя с равной
        # специфичностью проигрывали стилям страницы по порядку.
        self.assertGreater(body.index('mobile.css'), body.rindex('</style>'))

    def test_bottom_nav_links_are_alive(self):
        make_user('mobile_nav_user')
        self.client.login(username='mobile_nav_user', password=PASSWORD)
        body = self.client.get(reverse('main:dashboard')).content.decode()
        self.assertIn('bottom-nav', body)

        nav = body.split('class="bottom-nav"', 1)[1].split('</nav>', 1)[0]
        links = re.findall(r'href="([^"]+)"', nav)
        self.assertEqual(len(links), 5)
        for link in links:
            self.assertEqual(
                self.client.get(link).status_code, 200, f'{link} из нижней навигации не открывается',
            )

    def test_anonymous_keeps_login_links_on_phone(self):
        """
        Правило, прячущее верхнее меню на телефоне, не должно задевать
        гостя: нижней навигации у него нет, и эти две ссылки —
        единственные кнопки «Вход» и «Регистрация» на странице.
        """
        body = self.client.get(reverse('main:login')).content.decode()
        self.assertNotIn('nav-links-auth', body)
        self.assertIn(reverse('main:register'), body)

    def test_sections_hidden_on_phone_are_reachable(self):
        """
        Верхнее меню на телефоне скрыто целиком. Каждый его раздел
        обязан быть достижим иначе — из нижней навигации или из меню
        профиля, — иначе он просто исчезает: в установленном приложении
        нет даже адресной строки, чтобы ввести адрес руками.
        """
        make_user('mobile_reach_user')
        self.client.login(username='mobile_reach_user', password=PASSWORD)
        body = self.client.get(reverse('main:dashboard')).content.decode()

        hidden = body.split('nav-links-auth', 1)[1].split('</div>', 1)[0]
        hidden_links = set(re.findall(r'href="([^"]+)"', hidden))
        self.assertTrue(hidden_links)

        nav = body.split('class="bottom-nav"', 1)[1].split('</nav>', 1)[0]
        dropdown = body.split('dropdown-content', 1)[1].split('</div>', 1)[0]
        reachable = set(re.findall(r'href="([^"]+)"', nav + dropdown))

        self.assertEqual(
            hidden_links - reachable, set(),
            'раздел скрыт на телефоне и не продублирован нигде',
        )

    def test_apple_touch_icon_exists(self):
        from django.contrib.staticfiles import finders

        body = self.client.get(reverse('main:login')).content.decode()
        match = re.search(r'rel="apple-touch-icon"[^>]*href="([^"]+)"', body)
        self.assertIsNotNone(match, 'apple-touch-icon не объявлен')
        path = match.group(1).split('/static/', 1)[-1]
        self.assertIsNotNone(finders.find(path), f'иконка {path} не найдена')

    def test_apk_download_accepts_head(self):
        """Менеджеры загрузок Android начинают скачивание с HEAD."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'angelsheart.apk'
            apk.write_bytes(b'PK\x03\x04fake')
            with override_settings(ANDROID_APK_PATH=str(apk)):
                self.assertEqual(self.client.head(reverse('main:download_apk')).status_code, 200)

    def test_apk_page_shows_checksum(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / 'angelsheart.apk'
            apk.write_bytes(b'PK\x03\x04fake')
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            with override_settings(ANDROID_APK_PATH=str(apk)):
                cache.clear()
                self.assertContains(self.client.get(reverse('main:app_page')), digest)

    def test_apk_served_by_nginx_when_configured(self):
        """
        Отдача питоном занимает рабочий процесс на всё время передачи.
        Когда задана внутренняя локация, файл отдаёт nginx.
        """
        with override_settings(ANDROID_APK_INTERNAL_LOCATION='/internal-apk/angelsheart.apk'):
            response = self.client.get(reverse('main:download_apk'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Accel-Redirect'], '/internal-apk/angelsheart.apk')
        self.assertEqual(response.content, b'')


class MobileLayoutTests(TestCase):
    """
    Мобильная вёрстка и окна вместо браузерных.

    Все проверки здесь — про то, что видит человек на телефоне:
    обрезанный текст, системная плашка вместо окна сайта, молча
    отвалившийся скрипт.
    """

    TEMPLATES = Path(__file__).resolve().parent / 'templates'

    @staticmethod
    def _strip_comments(text):
        """Убирает комментарии, чтобы не ловить упоминания в объяснениях."""
        text = re.sub(r'\{#.*?#\}', '', text, flags=re.S)
        text = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', text, flags=re.S)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
        return text

    def test_no_browser_dialogs(self):
        """
        Ни alert, ни confirm, ни prompt.

        Системная плашка показывает адрес сайта в заголовке, замораживает
        страницу целиком и в установленном приложении выглядит как чужая
        ошибка. Вместо них — окно сайта: showAlertModal, showConfirmModal
        и атрибут data-confirm.
        """
        allowed = ('showAlertModal', 'showConfirmModal', 'deferred.prompt')
        offenders = []

        for template in self.TEMPLATES.rglob('*.html'):
            body = self._strip_comments(template.read_text(encoding='utf-8'))
            for number, line in enumerate(body.splitlines(), 1):
                for call in re.finditer(r'(?<![\w.])(alert|confirm|prompt)\s*\(', line):
                    fragment = line[max(0, call.start() - 20):call.end()]
                    if any(name in fragment for name in allowed):
                        continue
                    # window.alert = ... — это как раз подмена, а не вызов
                    if 'window.alert =' in line:
                        continue
                    offenders.append(f'{template.name}:{number}: {line.strip()[:70]}')

        self.assertEqual(offenders, [], 'остались браузерные окна:\n' + '\n'.join(offenders))

    def test_dangerous_actions_ask_confirmation(self):
        """
        Необратимые действия спрашивают подтверждение — и спрашивают им
        же, атрибутом data-confirm, а не выборочно.
        """
        must_confirm = {
            'my_withdrawals.html': 'отмена заявки на вывод',
            'my_debt.html': 'погашение задолженности',
            'my_fundraises.html': 'завершение и отмена сбора',
            'fundraise_detail.html': 'завершение и отмена сбора',
            'fundraise_manage.html': 'удаление документа',
            'moderate_fundraise.html': 'отклонение сбора',
            '2fa_setup.html': 'выключение двухфакторной проверки',
        }
        for name, what in must_confirm.items():
            body = (self.TEMPLATES / 'main' / name).read_text(encoding='utf-8')
            self.assertIn('data-confirm', body, f'{name}: {what} без подтверждения')

    def test_confirmation_machinery_present(self):
        """Атрибут data-confirm бесполезен без обработчика в base.html."""
        base = (self.TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertIn('data-confirm', base)
        self.assertIn('showAlertModal', base)
        self.assertIn('requestSubmit', base)
        # Кнопка формы должна отправлять форму именно собой: на выборе
        # действия модератора («одобрить» против «отклонить») держится
        # смысл страницы
        self.assertIn('form.requestSubmit(element)', base)

    def test_numbers_in_page_scripts_are_not_localized(self):
        """
        Число в коде страницы не должно печататься по русским правилам.

        `{{ balance|floatformat:2 }}` давало `const balance = 17500,50;` —
        синтаксическую ошибку. Браузер выбрасывает ВЕСЬ блок скрипта:
        на странице вывода средств не работали ни проверка суммы, ни
        переключение полей способа выплаты. Страница при этом выглядела
        совершенно рабочей.
        """
        user = make_user('layout_probe', balance='17500.50')
        UserVerification.objects.update_or_create(
            user=user, defaults={'level': 'full', 'verified_at': timezone.now()},
        )
        self.client.force_login(user)

        pages = ['/', '/transfer/', '/withdrawal/', '/topup/', '/history/', '/profile/']
        for path in pages:
            response = self.client.get(path)
            if response.status_code != 200:
                continue
            body = response.content.decode()
            for script in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', body, re.S):
                script = self._strip_comments(script)
                # Присваивание или сравнение с числом вида 17500,50
                bad = re.findall(r'[=<>(,]\s*-?\d+,\d+\s*[;),]', script)
                self.assertEqual(
                    bad, [],
                    f'{path}: число с запятой в коде страницы — скрипт не запустится: {bad}',
                )

    def test_tables_get_column_labels_on_phone(self):
        """
        Таблица из семи колонок на телефоне показывает две, а полосы
        прокрутки не видно — человек видит обрезанный текст. Строка
        должна разворачиваться в карточку «подпись — значение».
        """
        base = (self.TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertIn("setAttribute('data-label'", base)
        self.assertIn("classList.add('has-labels')", base)

        css = (Path(__file__).resolve().parent / 'static' / 'css' / 'mobile.css').read_text(
            encoding='utf-8')
        self.assertIn('table.has-labels', css)
        self.assertIn('attr(data-label)', css)
        # Запрет переноса превращал таблицу из двух колонок в ленту
        self.assertNotIn('white-space: nowrap;\n    }', css.split('.filter-strip')[0])

    def test_alert_stays_closable_without_network(self):
        """
        Без сети кнопка подтверждения гасится — но не в окне
        предупреждения: там она просто закрывает окно. Иначе человек
        оказывался заперт в окне, сообщающем о проблеме, а клавиши
        Escape на телефоне нет.
        """
        base = (self.TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertIn("classList.add('is-alert')", base)
        self.assertIn("classList.remove('is-alert')", base)

        css = (Path(__file__).resolve().parent / 'static' / 'css' / 'mobile.css').read_text(
            encoding='utf-8')
        self.assertIn('.confirm-modal:not(.is-alert) #confirmOkBtn', css)

    def test_window_onclick_not_overwritten(self):
        """
        Присвоение window.onclick затирало обработчик страницы
        регистрации, и окна Соглашения и Политики переставали
        закрываться кликом по подложке.
        """
        base = (self.TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertNotIn('window.onclick =', base)
        self.assertIn("window.addEventListener('click'", base)

    def test_destructive_button_is_not_first(self):
        """
        В окне подтверждения кнопка «Подтвердить» не должна стоять выше
        «Отмены»: для необратимых действий первой по взгляду оказывалась
        разрушительная, и порядок расходился с обходом клавиатурой.
        """
        css = (Path(__file__).resolve().parent / 'static' / 'css' / 'mobile.css').read_text(
            encoding='utf-8')
        self.assertNotIn('column-reverse', css)

    def test_pluralize_not_used_with_three_forms(self):
        """
        Фильтр pluralize понимает две формы, а по-русски их три: на трёх
        он молча возвращает пустую строку — выходило «У вас 1 активных
        заяв».
        """
        offenders = []
        for template in self.TEMPLATES.rglob('*.html'):
            body = template.read_text(encoding='utf-8')
            for match in re.finditer(r'pluralize:"([^"]*)"', body):
                if match.group(1).count(',') > 1:
                    offenders.append(f'{template.name}: {match.group(0)}')
        self.assertEqual(offenders, [], 'pluralize с тремя формами:\n' + '\n'.join(offenders))

    def test_table_labels_survive_colspan(self):
        """
        Ячейка с colspan занимает несколько колонок. Считать по номеру
        ячейки — значит сдвинуть все подписи после неё на чужие места.
        """
        base = (self.TEMPLATES / 'base.html').read_text(encoding='utf-8')
        self.assertIn("parseInt(cell.getAttribute('colspan')", base)
        self.assertIn('column += span', base)

    def test_long_words_wrap_on_wide_screen_too(self):
        """
        Название сбора без пробелов разносило страницу и на большом
        экране: 1977 пикселей при 1280. Данные те же самые, значит
        и правило переноса не должно быть заперто в медиазапросе.
        """
        css = (Path(__file__).resolve().parent / 'static' / 'css' / 'mobile.css').read_text(
            encoding='utf-8')
        before_media = css.split('@media', 1)[0]
        self.assertIn('overflow-wrap: anywhere', before_media)
        self.assertIn('overflow-wrap: break-word', before_media)
        # Подпись кнопки не переносим: «Отозвать» вставало как
        # «Отозват» и «ь» на отдельной строке
        self.assertIn('overflow-wrap: normal', before_media)

    def test_long_words_wrap_on_phone(self):
        """
        Название сбора без пробелов раздувало раскладку до 811 пикселей
        на экране шириной 320: браузер отдалял страницу до трети, и
        нечитаемым становился весь текст, а не только длинное слово.
        """
        css = (Path(__file__).resolve().parent / 'static' / 'css' / 'mobile.css').read_text(
            encoding='utf-8')
        self.assertIn('overflow-wrap: anywhere', css)
        # Переключатели нельзя растягивать до 44 пикселей: кружок повисал
        # над своей подписью
        self.assertIn('input:not([type="radio"]):not([type="checkbox"])', css)
        # Центрирование ленты фильтров прятало её начало без возможности долистать
        self.assertIn('justify-content: flex-start !important', css)
