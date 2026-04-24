from rest_framework import viewsets, generics, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.contrib.auth.models import User
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from main.models import Balance, Transaction, Fundraise, Donation, UserConsent
from .serializers import *
from .permissions import IsAuthorOrReadOnly, IsNotAuthor


# ==================== АУТЕНТИФИКАЦИЯ ====================

class RegisterView(generics.CreateAPIView):
    """Регистрация нового пользователя"""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_description="Регистрация нового пользователя",
        request_body=RegisterSerializer,
        responses={201: UserSerializer(), 400: "Ошибка валидации"}
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': UserSerializer(user).data,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(generics.GenericAPIView):
    """Авторизация пользователя"""
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_description="Авторизация пользователя",
        request_body=LoginSerializer,
        responses={200: "Токены", 401: "Ошибка авторизации"}
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': UserSerializer(user).data,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        return Response({'error': 'Неверные учетные данные'},
                        status=status.HTTP_401_UNAUTHORIZED)


class LogoutView(generics.GenericAPIView):
    """Выход из системы"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({'message': 'Успешный выход'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'message': 'Успешный выход'}, status=status.HTTP_200_OK)


# ==================== ПОЛЬЗОВАТЕЛИ ====================

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """Просмотр пользователей"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['username', 'email']
    ordering_fields = ['username', 'date_joined']
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Получить информацию о текущем пользователе"""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
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
    """Управление сборами средств"""
    queryset = Fundraise.objects.all()
    permission_classes = [IsAuthenticated, IsAuthorOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'status']
    search_fields = ['title', 'description', 'author__username']
    ordering_fields = ['created_at', 'current_amount', 'target_amount', 'donors_count']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return FundraiseCreateSerializer
        elif self.action == 'list':
            return FundraiseListSerializer
        return FundraiseDetailSerializer
    
    def perform_create(self, serializer):
        # Проверка на активный сбор
        if Fundraise.objects.filter(author=self.request.user, status='active').exists():
            from rest_framework import serializers
            raise serializers.ValidationError({"error": "У вас уже есть активный сбор"})
        serializer.save(author=self.request.user)
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def donate(self, request, pk=None):
        """Пожертвовать в сбор"""
        fundraise = self.get_object()
        
        # Проверка, что пользователь не автор
        if fundraise.author == request.user:
            return Response({'error': 'Нельзя пожертвовать в свой собственный сбор'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        if fundraise.status != 'active':
            return Response({'error': 'Сбор не активен'}, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = DonationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        amount = float(serializer.validated_data['amount'])
        
        # Проверка баланса
        if float(request.user.balance.amount) < amount:
            return Response({'error': 'Недостаточно средств'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with db_transaction.atomic():
                # Списание с баланса донатера
                donor_balance = Balance.objects.select_for_update().get(user=request.user)
                donor_balance.amount -= amount
                donor_balance.save()
                
                # Зачисление автору (с учетом комиссии 3%)
                commission = amount * 0.03
                author_amount = amount - commission
                author_balance = Balance.objects.select_for_update().get(user=fundraise.author)
                author_balance.amount += author_amount
                author_balance.save()
                
                # Обновление сбора
                fundraise.current_amount += amount
                fundraise.donors_count += 1
                fundraise.save()
                
                # Создание доната
                donation = Donation.objects.create(
                    donor=request.user,
                    fundraise=fundraise,
                    amount=amount,
                    message=serializer.validated_data.get('message', ''),
                    is_anonymous=serializer.validated_data.get('is_anonymous', False)
                )
                
                # Создание транзакции
                Transaction.objects.create(
                    sender=request.user,
                    receiver=fundraise.author,
                    amount=author_amount,
                    comment=f'Пожертвование на сбор "{fundraise.title}"',
                    status='completed',
                    is_donation=True,
                    fundraise_id=fundraise.id
                )
                
                return Response({
                    'message': 'Пожертвование успешно',
                    'donation': DonationSerializer(donation).data,
                    'commission': commission
                }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Завершить сбор (только для автора)"""
        fundraise = self.get_object()
        
        if fundraise.author != request.user:
            return Response({'error': 'Только автор может завершить сбор'},
                            status=status.HTTP_403_FORBIDDEN)
        
        if fundraise.status != 'active':
            return Response({'error': 'Сбор уже завершен'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        fundraise.status = 'completed'
        fundraise.save()
        
        return Response({'message': f'Сбор "{fundraise.title}" успешно завершен'})


# ==================== ДОНАТЫ ====================

class DonationViewSet(viewsets.ReadOnlyModelViewSet):
    """Просмотр донатов"""
    serializer_class = DonationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['fundraise_id', 'is_anonymous']
    ordering_fields = ['created_at', 'amount']
    
    def get_queryset(self):
        return Donation.objects.filter(donor=self.request.user)


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
        """Сделать перевод другому пользователю"""
        serializer = TransferSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        receiver = serializer.validated_data['receiver_username']
        amount = float(serializer.validated_data['amount'])
        comment = serializer.validated_data.get('comment', '')
        
        if receiver == request.user:
            return Response({'error': 'Нельзя перевести самому себе'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        if float(request.user.balance.amount) < amount:
            return Response({'error': 'Недостаточно средств'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with db_transaction.atomic():
                # Списание
                sender_balance = Balance.objects.select_for_update().get(user=request.user)
                sender_balance.amount -= amount
                sender_balance.save()
                
                # Зачисление
                receiver_balance = Balance.objects.select_for_update().get(user=receiver)
                receiver_balance.amount += amount
                receiver_balance.save()
                
                # Создание транзакции
                transaction = Transaction.objects.create(
                    sender=request.user,
                    receiver=receiver,
                    amount=amount,
                    comment=comment,
                    status='completed'
                )
                
                return Response(TransactionSerializer(transaction).data,
                                status=status.HTTP_201_CREATED)
        
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
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
@permission_classes([AllowAny])
def leaders_board(request):
    """Рейтинг пользователей"""
    top_senders = User.objects.filter(
        sent_transactions__status='completed'
    ).annotate(
        total_sent=Sum('sent_transactions__amount')
    ).order_by('-total_sent')[:10]
    
    top_receivers = User.objects.filter(
        received_transactions__status='completed'
    ).annotate(
        total_received=Sum('received_transactions__amount')
    ).order_by('-total_received')[:10]
    
    # Получаем балансы для пользователей
    senders_data = []
    for user in top_senders:
        senders_data.append({
            'id': user.id,
            'username': user.username,
            'total_sent': user.total_sent,
            'balance': user.balance.amount if hasattr(user, 'balance') else 0
        })
    
    receivers_data = []
    for user in top_receivers:
        receivers_data.append({
            'id': user.id,
            'username': user.username,
            'total_received': user.total_received,
            'balance': user.balance.amount if hasattr(user, 'balance') else 0
        })
    
    return Response({
        'top_senders': senders_data,
        'top_receivers': receivers_data,
    })