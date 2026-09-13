import logging

from django.contrib.auth.models import User
from django.db.models import Q, Sum
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from rest_framework import filters, generics, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from main import services
from main.models import Balance, Donation, Fundraise, Transaction, UserConsent
from main.services import InsufficientFunds, OperationRejected
from main.views import record_consents

from .permissions import IsAuthorOrReadOnly
from .serializers import (
    ConsentSerializer, CurrentUserSerializer, DonationCreateSerializer,
    DonationSerializer, FundraiseCreateSerializer, FundraiseDetailSerializer,
    FundraiseListSerializer, LoginSerializer, REQUIRED_CONSENTS,
    RegisterSerializer, TransactionSerializer, TransferSerializer, UserSerializer,
)

logger = logging.getLogger('withdrawals')


class DonateThrottle(ScopedRateThrottle):
    """Отдельный лимит на пожертвования (ставка 'donate' в настройках)."""
    scope = 'donate'


# ==================== АУТЕНТИФИКАЦИЯ ====================

class RegisterView(generics.CreateAPIView):
    """Регистрация нового пользователя"""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    @swagger_auto_schema(
        operation_description='Регистрация нового пользователя',
        request_body=RegisterSerializer,
        responses={201: CurrentUserSerializer(), 400: 'Ошибка валидации'}
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Пользователь и его согласия создаются одной транзакцией.
        # Раньше API-регистрация не фиксировала согласия вообще.
        consent_types = list(REQUIRED_CONSENTS)
        if serializer.validated_data.get('consent_distribution'):
            consent_types.append('distribution')

        with services.db_transaction.atomic():
            user = serializer.save()
            record_consents(request, user, consent_types)

        # Письмо подтверждения отправляется и здесь: раньше оно уходило
        # только при регистрации через сайт, и пользователь мобильного
        # приложения упирался в запрет вывода средств без объяснения,
        # не имея способа подтвердить адрес.
        from main import email_confirmation

        confirmation_sent = email_confirmation.send_confirmation(user)

        refresh = RefreshToken.for_user(user)
        return Response({
            'user': CurrentUserSerializer(user).data,
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'email_confirmation_sent': confirmation_sent,
            'email_confirmed': False,
        }, status=status.HTTP_201_CREATED)


class ResendEmailConfirmationView(generics.GenericAPIView):
    """Повторная отправка письма с подтверждением адреса."""

    permission_classes = [IsAuthenticated]
    serializer_class = drf_serializers.Serializer

    @swagger_auto_schema(
        operation_description='Отправить письмо для подтверждения адреса ещё раз',
        responses={200: 'Письмо отправлено', 429: 'Слишком часто'},
    )
    def post(self, request):
        from main import email_confirmation
        from main.models import EmailConfirmation

        confirmation = EmailConfirmation.for_user(request.user)
        if confirmation.is_actually_confirmed:
            return Response({'message': 'Адрес уже подтверждён', 'email_confirmed': True})
        if not request.user.email:
            return Response({'error': 'В учётной записи не указан адрес'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not confirmation.can_resend:
            return Response(
                {'error': 'Письмо уже отправлено, повторите через несколько минут'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if email_confirmation.send_confirmation(request.user):
            return Response({'message': f'Письмо отправлено на {request.user.email}'})
        return Response({'error': 'Не удалось отправить письмо'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class LoginView(generics.GenericAPIView):
    """Авторизация пользователя"""
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    # Отдельный лимит на подбор пароля: общий anon-лимит (100/день)
    # слишком щедр для формы входа
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    @swagger_auto_schema(
        operation_description='Авторизация пользователя',
        request_body=LoginSerializer,
        responses={200: 'Токены', 401: 'Ошибка авторизации', 403: 'Требуется 2FA'}
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({'error': 'Неверные учетные данные'},
                            status=status.HTTP_401_UNAUTHORIZED)

        user = serializer.validated_data

        # Если у пользователя включена 2FA, пароль сам по себе доступа не даёт.
        # Раньше веб-вход обходил 2FA, а API про неё вовсе не знал.
        from main.models import TwoFactorAuth
        if TwoFactorAuth.objects.filter(user=user, is_enabled=True).exists():
            code = request.data.get('code')
            from main.payments.security import TwoFactorAuthService
            two_factor = TwoFactorAuth.objects.get(user=user)
            valid = (
                TwoFactorAuthService.verify_code(two_factor.secret_key, code)
                or TwoFactorAuthService.consume_backup_code(two_factor, code)
            )
            if not valid:
                return Response(
                    {'error': 'Требуется код двухфакторной аутентификации',
                     'code_required': True},
                    status=status.HTTP_403_FORBIDDEN,
                )

        refresh = RefreshToken.for_user(user)
        return Response({
            'user': CurrentUserSerializer(user).data,
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        })


class LogoutView(generics.GenericAPIView):
    """Выход из системы с отзывом refresh-токена"""
    permission_classes = [IsAuthenticated]
    serializer_class = None

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({'error': 'Не передан refresh-токен'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            RefreshToken(refresh_token).blacklist()
        except Exception:
            # Раньше здесь был except Exception: pass, и пользователю
            # рапортовали об успешном выходе, хотя token_blacklist не был
            # подключён и токен продолжал работать все 7 дней.
            logger.warning('Не удалось отозвать refresh-токен пользователя %s', request.user.pk)
            return Response({'error': 'Токен недействителен или уже отозван'},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': 'Успешный выход'}, status=status.HTTP_200_OK)


# ==================== ПОЛЬЗОВАТЕЛИ ====================

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Просмотр пользователей.

    Поиск по email убран, email и баланс из выдачи убраны: эндпоинт
    отдавал адреса и остатки всех пользователей любому авторизованному.
    """
    queryset = User.objects.filter(is_active=True).order_by('username')
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['username']
    ordering_fields = ['username', 'date_joined']

    @action(detail=False, methods=['get'])
    def me(self, request):
        """Собственный профиль: единственное место, где видны email и баланс."""
        return Response(CurrentUserSerializer(request.user).data)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Получить статистику пользователя"""
        total_sent = Transaction.objects.filter(
            sender=request.user, status='completed'
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        total_received = Transaction.objects.filter(
            receiver=request.user, status='completed'
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        return Response({
            'balance': request.user.balance.amount,
            'total_sent': total_sent,
            'total_received': total_received,
            'transactions_count': Transaction.objects.filter(
                Q(sender=request.user) | Q(receiver=request.user)
            ).count(),
        })


# ==================== СБОРЫ ====================

class FundraiseViewSet(viewsets.ModelViewSet):
    """
    Управление сборами средств.

    Правила допуска здесь те же, что и в веб-интерфейсе, и это не
    дублирование ради симметрии: раньше API знал только `Fundraise.objects.all()`
    и позволял в обход модерации всё — читать чужие черновики, создавать сбор
    без согласия на распространение ПДн, править одобренный текст и удалять
    сбор вместе с пожертвованиями.
    """

    queryset = Fundraise.objects.all()
    permission_classes = [IsAuthenticated, IsAuthorOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'status']
    search_fields = ['title', 'description', 'author__username']
    ordering_fields = ['created_at', 'current_amount', 'target_amount', 'donors_count']
    # Удаление сбора недоступно: вместе с ним каскадом исчезали пожертвования
    # и обязательство вернуть их. Сбор отменяется, а не удаляется.
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        """Видно опубликованное плюс собственные сборы; сотрудникам — всё."""
        user = self.request.user
        if user.is_staff:
            return Fundraise.objects.all()
        # Жертвователь видит сбор, в который отдал деньги, даже если тот
        # снят с публикации: иначе веб и API снова расходятся — в вебе
        # доступ донора есть.
        return (
            Fundraise.published()
            | Fundraise.objects.filter(author=user)
            | Fundraise.objects.filter(donations__donor=user)
        ).distinct()

    def get_serializer_class(self):
        if self.action == 'create':
            return FundraiseCreateSerializer
        elif self.action == 'list':
            return FundraiseListSerializer
        return FundraiseDetailSerializer

    def perform_create(self, serializer):
        from rest_framework import serializers

        from main.models import UserConsent

        # Публикация сбора делает имя автора и описание общедоступными:
        # ст. 10.1 152-ФЗ требует отдельного согласия
        if not UserConsent.has_active_consent(self.request.user, 'distribution'):
            raise serializers.ValidationError({
                'error': 'Нужно согласие на распространение персональных данных',
            })

        if Fundraise.objects.filter(
            author=self.request.user, status__in=Fundraise.UNFINISHED_STATUSES,
        ).exists():
            raise serializers.ValidationError({'error': 'У вас уже есть незавершённый сбор'})

        # Сбор создаётся черновиком и публикуется только после проверки
        serializer.save(author=self.request.user, status='draft', moderation_status='draft')

    def perform_update(self, serializer):
        """Правка проверенных сведений возвращает сбор на модерацию."""
        fundraise = self.get_object()
        if not fundraise.is_editable:
            from rest_framework import serializers
            raise serializers.ValidationError({
                'error': 'Сбор на проверке — дождитесь решения модератора',
            })

        changed = {
            field for field in Fundraise.MODERATED_FIELDS
            if field in serializer.validated_data
            and serializer.validated_data[field] != getattr(fundraise, field)
        }
        updated = serializer.save()
        if changed and updated.moderation_status == 'approved':
            updated.reset_moderation()
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            throttle_classes=[DonateThrottle])
    def donate(self, request, pk=None):
        """
        Пожертвовать в сбор.

        Вся арифметика ушла в services.donate. Прежняя реализация считала
        на float (`amount = float(...)`, `commission = amount * 0.03`) и
        падала с TypeError на `Decimal -= float` — эндпоинт не работал
        вообще. Заодно ставка комиссии здесь была своя, 3 %, а в веб-версии
        0 % — одна операция давала разный результат.
        """
        fundraise = self.get_object()

        serializer = DonationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            donation = services.donate(
                donor=request.user,
                fundraise=fundraise,
                amount=serializer.validated_data['amount'],
                message=serializer.validated_data.get('message', ''),
                is_anonymous=serializer.validated_data.get('is_anonymous', False),
            )
        except (InsufficientFunds, OperationRejected) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Ошибка пожертвования в сбор #%s через API', pk)
            return Response({'error': 'Внутренняя ошибка'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        commission = getattr(donation, 'commission', None)
        return Response({
            'message': 'Пожертвование успешно',
            'donation': DonationSerializer(donation, context={'request': request}).data,
            'commission': str(commission.amount) if commission else '0.00',
        }, status=status.HTTP_201_CREATED)

    def create(self, request, *args, **kwargs):
        """
        Создание сбора.

        Ответ отдаётся подробным сериализатором: сериализатор создания
        не содержит даже id, и клиент не знал, какой сбор он только что
        создал и что с ним делать дальше.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        detail = FundraiseDetailSerializer(serializer.instance, context={'request': request})
        return Response(detail.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """
        Отправить сбор на проверку.

        Без этого действия сбор, созданный через API, оставался черновиком
        навсегда: опубликовать его было нечем, а создать второй мешало
        правило «один незавершённый сбор».
        """
        from main import moderation

        fundraise = self.get_object()

        if fundraise.author != request.user:
            return Response({'error': 'Только автор может отправить сбор на проверку'},
                            status=status.HTTP_403_FORBIDDEN)

        if not fundraise.is_editable:
            return Response({'error': 'Сбор уже на проверке'},
                            status=status.HTTP_400_BAD_REQUEST)

        problems = moderation.check_can_submit(fundraise)
        if problems:
            return Response({'error': 'Сбор не готов к проверке', 'problems': problems},
                            status=status.HTTP_400_BAD_REQUEST)

        fundraise.submit_for_moderation()
        return Response({
            'message': 'Сбор отправлен на проверку',
            'moderation_status': fundraise.moderation_status,
        })

    @action(detail=True, methods=['get'])
    def moderation_requirements(self, request, pk=None):
        """
        Что мешает отправить сбор на проверку.

        Клиенту нужен способ показать автору список проблем до отправки,
        а не только в ответе с ошибкой.
        """
        from main import moderation

        fundraise = self.get_object()
        if fundraise.author != request.user:
            return Response({'error': 'Доступно только автору'},
                            status=status.HTTP_403_FORBIDDEN)

        return Response({
            'moderation_status': fundraise.moderation_status,
            'moderation_comment': fundraise.moderation_comment,
            'can_submit': fundraise.is_editable and not moderation.check_can_submit(fundraise),
            'problems': moderation.check_can_submit(fundraise),
        })

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Завершить сбор (только для автора)"""
        fundraise = self.get_object()

        if fundraise.author != request.user:
            return Response({'error': 'Только автор может завершить сбор'},
                            status=status.HTTP_403_FORBIDDEN)

        if fundraise.status != 'active':
            return Response({'error': 'Сбор уже завершён'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Завершить можно только проверенный сбор: иначе так закрывался
        # сбор, снятый с публикации после правки, и отклонить его
        # с возвратом денег становилось нельзя.
        if fundraise.moderation_status != 'approved':
            return Response(
                {'error': 'Сбор не прошёл проверку — завершить его нельзя, только отменить'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fundraise.status = 'completed'
        fundraise.save(update_fields=['status'])

        return Response({'message': f'Сбор «{fundraise.title}» успешно завершён'})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Отменить сбор с возвратом пожертвований донорам.

        В API этой операции не было вовсе, а в веб-версии отмена оставляла
        деньги у автора.
        """
        fundraise = self.get_object()

        if fundraise.author != request.user:
            return Response({'error': 'Только автор может отменить сбор'},
                            status=status.HTTP_403_FORBIDDEN)

        if fundraise.status != 'active':
            return Response({'error': 'Сбор уже завершён или отменён'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            with services.db_transaction.atomic():
                result = services.refund_donations(fundraise)
                fundraise.status = 'cancelled'
                fundraise.save(update_fields=['status'])
        except Exception:
            logger.exception('Ошибка отмены сбора #%s через API', pk)
            return Response({'error': 'Внутренняя ошибка'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'message': f'Сбор «{fundraise.title}» отменён',
            'refunded': str(result['refunded']),
            'shortfall': str(result['shortfall']),
        })


# ==================== ДОНАТЫ ====================

class DonationViewSet(viewsets.ReadOnlyModelViewSet):
    """Просмотр донатов"""
    serializer_class = DonationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['fundraise', 'is_anonymous']
    ordering_fields = ['created_at', 'amount']

    def get_queryset(self):
        return Donation.objects.filter(donor=self.request.user).select_related('fundraise', 'donor')


# ==================== ТРАНЗАКЦИИ ====================

class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """Просмотр транзакций"""
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'is_donation']
    search_fields = ['comment']
    ordering_fields = ['created_at', 'amount']
    
    def get_queryset(self):
        return Transaction.objects.filter(
            Q(sender=self.request.user) | Q(receiver=self.request.user)
        ).select_related('sender', 'receiver').order_by('-created_at')
    
    @action(detail=False, methods=['post'])
    def transfer(self, request):
        """
        Перевод другому пользователю.

        Прежняя реализация переводила сумму в float и падала с
        `TypeError: unsupported operand type(s) for -=: 'decimal.Decimal'
        and 'float'` — эндпоинт всегда отвечал 500.
        """
        serializer = TransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            transaction = services.transfer(
                sender=request.user,
                receiver=serializer.validated_data['receiver_username'],
                amount=serializer.validated_data['amount'],
                comment=serializer.validated_data.get('comment', ''),
            )
        except (InsufficientFunds, OperationRejected) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Ошибка перевода через API от %s', request.user.pk)
            return Response({'error': 'Внутренняя ошибка'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(TransactionSerializer(transaction).data,
                        status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Статистика транзакций"""
        total_sent = Transaction.objects.filter(
            sender=request.user, status='completed'
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        total_received = Transaction.objects.filter(
            receiver=request.user, status='completed'
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        
        return Response({
            'total_sent': total_sent,
            'total_received': total_received,
            'total_count': self.get_queryset().count(),
        })


# ==================== КОНСЕНТЫ ====================

class ConsentViewSet(viewsets.ReadOnlyModelViewSet):
    """Управление согласиями пользователя"""
    serializer_class = ConsentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return UserConsent.objects.filter(user=self.request.user, is_accepted=True)


# ==================== СТАТИСТИКА ====================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    """Главная статистика для дашборда"""
    recent_transactions = Transaction.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).order_by('-created_at')[:10]
    
    total_sent = Transaction.objects.filter(
        sender=request.user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    total_received = Transaction.objects.filter(
        receiver=request.user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    data = {
        'balance': request.user.balance.amount,
        'total_sent': total_sent,
        'total_received': total_received,
        'recent_transactions': TransactionSerializer(recent_transactions, many=True).data,
    }
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leaders_board(request):
    """
    Рейтинг пользователей.

    Было permission_classes([AllowAny]) — эндпоинт без всякой авторизации
    отдавал имена, обороты и **балансы** пользователей. Балансы убраны
    совсем, пополнения и возвраты исключены из оборота.
    """
    sent_filter = Q(sent_transactions__status='completed') & ~Q(
        sent_transactions__kind__in=['topup', 'refund']
    )
    received_filter = Q(received_transactions__status='completed') & ~Q(
        received_transactions__kind__in=['topup', 'refund']
    )

    top_senders = User.objects.annotate(
        total_sent=Sum('sent_transactions__amount', filter=sent_filter)
    ).filter(total_sent__gt=0).order_by('-total_sent')[:10]

    top_receivers = User.objects.annotate(
        total_received=Sum('received_transactions__amount', filter=received_filter)
    ).filter(total_received__gt=0).order_by('-total_received')[:10]

    return Response({
        'top_senders': [
            {'id': u.id, 'username': u.username, 'total_sent': u.total_sent}
            for u in top_senders
        ],
        'top_receivers': [
            {'id': u.id, 'username': u.username, 'total_received': u.total_received}
            for u in top_receivers
        ],
    })