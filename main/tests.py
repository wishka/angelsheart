"""
Тесты на дефекты, найденные аудитом.

Каждый класс закрывает конкретную находку: тест падал бы на коде до
исправления. Файл заменяет прежний tests.py, в котором было три пустых
строки — именно поэтому все 14 дефектов дожили до рабочей ветки.
"""

import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from main import moderation, services
from main import restrictions as restrictions_service
from main.models import (
    AccountRestriction, add_business_days, DataBreachIncident,
    Balance, CommissionTransaction, Donation, Fundraise, FundraiseDocument,
    KYCDocument, PaymentTransaction, PersonalDataAccessLog, SecurityLog,
    Transaction, TwoFactorAuth, UserConsent, UserVerification, WithdrawalRequest,
)
from main.payments.security import TwoFactorAuthService

PASSWORD = 'Sunrise-Harbor-42'
TRUSTED_IP = '185.71.76.1'  # из диапазона ЮKassa


def make_user(username, balance='0'):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password=PASSWORD,
    )
    Balance.objects.filter(user=user).update(amount=Decimal(balance))
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


class ExternalResourcesDisclosureTests(BaseCase):
    """Документ обязан говорить правду о трансграничной передаче."""

    @override_settings(USE_EXTERNAL_CDN=True)
    def test_policy_discloses_transfer_when_cdn_enabled(self):
        body = self.client.get(reverse('main:privacy_policy')).content.decode()
        self.assertIn('Трансграничная передача осуществляется', body)
        self.assertIn('Google', body)

    @override_settings(USE_EXTERNAL_CDN=False)
    def test_policy_denies_transfer_when_cdn_disabled(self):
        body = self.client.get(reverse('main:privacy_policy')).content.decode()
        self.assertIn('не осуществляется', body)
        self.assertNotIn('fonts.googleapis.com', body)


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
