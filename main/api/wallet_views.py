"""
Кошелёк в мобильном API: пополнение, вывод, верификация, восстановление
пароля.

Правила — в main/wallet.py, те же, что у сайта. Здесь только разбор
запроса и ответ JSON. Отказы приходят текстом в поле error — приложение
показывает его как есть.
"""

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from main import wallet
from main.models import Balance, KYCDocument, PaymentTransaction, SecurityLog, UserVerification, WithdrawalRequest
from main.payments.verification import KYCService
from main.services import InsufficientFunds, OperationRejected
from main.utils.request_meta import get_request_meta


def refuse(error):
    return Response({'error': str(error)}, status=status.HTTP_400_BAD_REQUEST)


def money(value):
    return f'{value:.2f}'


def withdrawal_payload(withdrawal):
    return {
        'id': withdrawal.pk,
        'amount': money(withdrawal.amount),
        'payment_method': withdrawal.payment_method,
        'payment_method_display': withdrawal.get_payment_method_display(),
        'details': withdrawal.payment_details_masked,
        'status': withdrawal.status,
        'status_display': withdrawal.get_status_display(),
        'created_at': withdrawal.created_at.isoformat(),
        'can_cancel': withdrawal.status == 'pending',
    }


class PasswordResetView(APIView):
    """
    POST {email} — письмо со ссылкой на восстановление пароля.

    Ответ одинаковый, есть такой адрес или нет: иначе форма отвечала бы
    на вопрос «зарегистрирован ли этот email». Ссылка из письма ведёт на
    сайт — там и задаётся новый пароль, как и раньше.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_reset'

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        form = PasswordResetForm({'email': email})
        if not form.is_valid():
            return Response({'error': 'Введите адрес почты'}, status=status.HTTP_400_BAD_REQUEST)
        ip_address, user_agent = get_request_meta(request)
        SecurityLog.objects.create(
            action='password_change', username_attempted=email[:150],
            ip_address=ip_address, user_agent=user_agent,
            details={'event': 'password_reset_requested', 'channel': 'api'},
        )
        form.save(
            request=request,
            use_https=request.is_secure(),
            email_template_name='emails/password_reset.txt',
            html_email_template_name='emails/password_reset.html',
            subject_template_name='emails/password_reset_subject.txt',
        )
        return Response({
            'message': f'Если адрес {email} зарегистрирован, на него отправлено письмо '
                       f'со ссылкой. Новый пароль задаётся по ссылке в браузере.',
        })


class WalletView(APIView):
    """GET — баланс и всё, что нужно формам пополнения и вывода."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limits = wallet.withdrawal_limits(request.user)
        from main.forms import available_withdrawal_methods
        return Response({
            'balance': money(Balance.objects.get(user=request.user).amount),
            'topup': {
                'min_amount': money(settings.MIN_TOPUP_AMOUNT),
                'max_amount': money(settings.MAX_TOPUP_AMOUNT),
                'methods': [{'id': k, 'title': v} for k, v in PaymentTransaction.METHOD_CHOICES],
                # Тестовый режим: деньги зачисляются сразу, без ЮKassa
                'simulated': settings.ALLOW_SIMULATED_TOPUP,
            },
            'withdraw': {
                'min_amount': money(limits['min_amount']),
                'max_amount': money(limits['max_amount']),
                'methods': [{'id': k, 'title': v} for k, v in available_withdrawal_methods()],
                'sbp_banks': [{'id': k, 'title': v} for k, v in sorted(settings.SBP_BANKS.items(), key=lambda i: i[1])],
            },
            'verification_level': limits['verification_level'],
        })


class TopUpView(APIView):
    """
    POST {amount, payment_method}.

    Ответ status=redirect с confirmation_url — приложение открывает
    страницу оплаты ЮKassa в браузере; деньги зачисляются по вебхуку.
    В тестовом режиме (ALLOW_SIMULATED_TOPUP) — status=completed сразу.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'donate'

    def post(self, request):
        from main.views import parse_amount
        try:
            amount = parse_amount(request.data.get('amount'), 'Сумма пополнения')
            if settings.ALLOW_SIMULATED_TOPUP:
                wallet.simulated_topup(request.user, amount)
                request.user.balance.refresh_from_db()
                return Response({
                    'status': 'completed',
                    'message': f'Баланс пополнен на {money(amount)} ₽ (тестовый режим)',
                    'balance': money(Balance.objects.get(user=request.user).amount),
                })
            url = wallet.create_topup_payment(
                request.user, amount, request.data.get('payment_method', 'card'), request,
            )
        except OperationRejected as error:
            return refuse(error)
        return Response({
            'status': 'redirect',
            'confirmation_url': url,
            'message': 'Откроется страница оплаты. После оплаты баланс обновится автоматически.',
        })


class WithdrawalsView(APIView):
    """GET — мои заявки на вывод; POST — новая заявка (поля как у формы сайта)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = WithdrawalRequest.objects.filter(user=request.user).order_by('-created_at')[:50]
        return Response({'results': [withdrawal_payload(w) for w in items]})

    def post(self, request):
        data = {key: request.data.get(key, '') for key in (
            'payment_method', 'amount', 'card_number', 'card_holder', 'expiry_date',
            'phone_number', 'bank_id', 'wallet_number',
        )}
        try:
            withdrawal = wallet.create_withdrawal(request.user, data, request)
        except (InsufficientFunds, OperationRejected) as error:
            return refuse(error)
        payload = withdrawal_payload(withdrawal)
        payload['message'] = (f'Заявка на вывод {money(withdrawal.amount)} ₽ создана. '
                              f'Статус: {withdrawal.get_status_display()}')
        return Response(payload, status=status.HTTP_201_CREATED)


class WithdrawalCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        withdrawal = get_object_or_404(WithdrawalRequest, pk=pk, user=request.user)
        if not withdrawal.cancel():
            return refuse('Невозможно отменить заявку в текущем статусе')
        return Response({'message': f'Заявка #{withdrawal.pk} отменена, средства возвращены на баланс',
                         **withdrawal_payload(withdrawal)})


class VerificationView(APIView):
    """
    GET — уровень, лимиты, поданы ли данные, загруженные документы.
    POST {full_name, birth_date, passport_series, passport_number} —
    данные на проверку. Сами паспортные данные обратно не отдаются никогда:
    только маска.
    """

    permission_classes = [IsAuthenticated]

    def _status(self, user):
        verification, _ = UserVerification.objects.get_or_create(user=user)
        return {
            'level': verification.level,
            'level_display': verification.get_level_display(),
            'submitted': verification.submitted_at is not None,
            'passport_masked': verification.passport_masked,
            'limits': {k: money(v) for k, v in KYCService.get_verification_limits(verification.level).items()},
            'documents': [
                {'id': d.pk, 'type': d.document_type, 'type_display': d.get_document_type_display(),
                 'status': d.status, 'status_display': d.get_status_display(),
                 'uploaded_at': d.uploaded_at.isoformat()}
                for d in KYCDocument.objects.filter(user=user)
            ],
            'document_types': [{'id': k, 'title': v} for k, v in KYCDocument.DOCUMENT_TYPES],
        }

    def get(self, request):
        return Response(self._status(request.user))

    def post(self, request):
        data = request.data
        try:
            wallet.submit_verification(
                request.user, data.get('full_name'), data.get('birth_date'),
                data.get('passport_series'), data.get('passport_number'), request,
            )
        except OperationRejected as error:
            return refuse(error)
        payload = self._status(request.user)
        payload['message'] = ('Данные отправлены на проверку. Загрузите скан документа — '
                              'уровень повысится после проверки администратором.')
        return Response(payload)


class VerificationDocumentView(VerificationView):
    """POST multipart: document_type, document_image, document_number (необязательно)."""

    def get(self, request):
        return Response(self._status(request.user))

    def post(self, request):
        try:
            wallet.upload_kyc_document(
                request.user, request.data.get('document_type'), request.FILES.get('document_image'),
                request.data.get('document_number', ''), request,
            )
        except OperationRejected as error:
            return refuse(error)
        payload = self._status(request.user)
        payload['message'] = 'Документ загружен на проверку'
        return Response(payload, status=status.HTTP_201_CREATED)
