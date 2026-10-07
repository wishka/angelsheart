"""
Мобильный API кошелька: те же правила, что у сайта (main/wallet.py).
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from main.models import Balance, KYCDocument, UserVerification, WithdrawalRequest
from main.tests import BaseCase, make_user


class WalletApiTests(BaseCase):
    def setUp(self):
        super().setUp()
        # Перечитать из базы: make_user меняет баланс запросом, а у объекта
        # остаётся закешированный при создании нулевой баланс
        self.user = User.objects.get(pk=make_user('api_wallet', '100000').pk)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.card = {
            'payment_method': 'card', 'card_number': '4111111111111111',
            'card_holder': 'IVAN IVANOV', 'expiry_date': '12/29',
        }

    def test_wallet_overview(self):
        data = self.api.get('/api/wallet/').json()
        self.assertEqual(data['balance'], '100000.00')
        self.assertEqual(data['verification_level'], 'unverified')
        self.assertTrue(data['topup']['methods'])

    @override_settings(ALLOW_SIMULATED_TOPUP=True)
    def test_simulated_topup(self):
        response = self.api.post('/api/wallet/topup/', {'amount': '150'}, format='json')
        self.assertEqual(response.json()['status'], 'completed')
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100150'))

    @override_settings(ALLOW_SIMULATED_TOPUP=False)
    def test_real_topup_returns_payment_page(self):
        fake = {'success': True, 'payment_id': 'pay-1', 'confirmation_url': 'https://yoomoney.ru/pay/1',
                'status': 'pending'}
        with mock.patch('main.payments.yookassa.YooKassaProvider.__init__', return_value=None), \
                mock.patch('main.payments.yookassa.YooKassaProvider.create_payment', return_value=fake):
            response = self.api.post('/api/wallet/topup/', {'amount': '150', 'payment_method': 'card'},
                                     format='json')
        self.assertEqual(response.json(), {
            'status': 'redirect', 'confirmation_url': 'https://yoomoney.ru/pay/1',
            'message': 'Откроется страница оплаты. После оплаты баланс обновится автоматически.',
        })
        # Деньги до вебхука не зачисляются
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))

    def test_topup_bad_amount(self):
        response = self.api.post('/api/wallet/topup/', {'amount': 'много'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_withdrawal_limit_applies_like_on_site(self):
        response = self.api.post('/api/wallet/withdrawals/', dict(self.card, amount='100000'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WithdrawalRequest.objects.exists())

    def test_withdrawal_create_list_cancel(self):
        response = self.api.post('/api/wallet/withdrawals/', dict(self.card, amount='500'), format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['details'], '****1111')
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('99500'))
        listed = self.api.get('/api/wallet/withdrawals/').json()['results']
        self.assertTrue(listed[0]['can_cancel'])
        cancelled = self.api.post(f"/api/wallet/withdrawals/{listed[0]['id']}/cancel/").json()
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(Balance.objects.get(user=self.user).amount, Decimal('100000'))

    def test_bad_card_rejected(self):
        payload = dict(self.card, amount='500', card_number='4111111111111112')
        self.assertEqual(self.api.post('/api/wallet/withdrawals/', payload, format='json').status_code, 400)

    def test_verification_rules(self):
        base = {'full_name': 'Иванов Иван Иванович', 'passport_series': '4510', 'passport_number': '123456'}
        response = self.api.post('/api/verification/', dict(base, birth_date='01.01.2015'), format='json')
        self.assertIn('совершеннолетним', response.json()['error'])
        response = self.api.post('/api/verification/', dict(base, birth_date='01.01.1990'), format='json')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['submitted'])
        # Уровень сам по себе не повышается — только администратор
        self.assertEqual(data['level'], 'unverified')
        self.assertNotIn('123456', str(data))
        self.assertEqual(UserVerification.objects.get(user=self.user).passport_number, '123456')

    def test_document_upload(self):
        scan = SimpleUploadedFile('p.jpg', b'\xff\xd8\xff' + b'0' * 100, content_type='image/jpeg')
        response = self.api.post('/api/verification/documents/',
                                 {'document_type': 'passport', 'document_image': scan}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(KYCDocument.objects.get().status, 'pending')
        bad = SimpleUploadedFile('x.exe', b'MZ', content_type='application/octet-stream')
        response = self.api.post('/api/verification/documents/',
                                 {'document_type': 'passport', 'document_image': bad}, format='multipart')
        self.assertEqual(response.status_code, 400)


class PasswordResetApiTests(BaseCase):
    def test_same_answer_for_known_and_unknown(self):
        make_user('reset_me')
        client = APIClient()
        known = client.post('/api/auth/password-reset/', {'email': 'reset_me@example.com'}, format='json')
        unknown = client.post('/api/auth/password-reset/', {'email': 'nobody@example.com'}, format='json')
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['reset_me@example.com'])

    def test_empty_email(self):
        self.assertEqual(APIClient().post('/api/auth/password-reset/', {'email': ''}, format='json').status_code, 400)
