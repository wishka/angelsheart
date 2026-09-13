from decimal import Decimal

from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from main.models import Balance, Donation, Fundraise, Transaction, UserConsent

# Согласия, обязательные для регистрации. Веб-форма их требовала,
# а API-регистрация не собирала вообще — пользователь заводился без
# единой записи UserConsent, то есть без правового основания обработки.
# Список берётся из модели: держать его отдельной строкой здесь означало
# рассинхрон при добавлении нового обязательного согласия (так и вышло —
# сюда был записан устаревший тип 'privacy' вместо 'data_processing').
REQUIRED_CONSENTS = list(UserConsent.REQUIRED_TYPES) + ['cookies']


# ==================== АУТЕНТИФИКАЦИЯ ====================

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)
    # Имена полей отражают правовое основание, а не документ: акцепт оферты
    # и согласие на обработку ПДн — разные вещи (ч. 1 ст. 9 152-ФЗ)
    accept_terms = serializers.BooleanField(write_only=True)
    consent_data_processing = serializers.BooleanField(write_only=True)
    consent_distribution = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = User
        fields = [
            'username', 'email', 'password', 'password2',
            'accept_terms', 'consent_data_processing', 'consent_distribution',
        ]
        extra_kwargs = {'email': {'required': True, 'allow_blank': False}}

    def validate_email(self, value):
        # Email — идентификатор для восстановления доступа, дубликаты недопустимы
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Пользователь с таким email уже зарегистрирован')

        # Та же проверка, что и на веб-форме: без неё регистрация через API
        # оставалась лазейкой для одноразовых ящиков.
        from main.utils.email_domains import check_email_domain

        domain_error = check_email_domain(value, allow_typo=self.initial_data.get(
            'email_typo_confirmed', False,
        ))
        if domain_error:
            raise serializers.ValidationError(domain_error)
        return value

    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError({'password2': 'Пароли не совпадают'})

        # Раньше проверялась только длина: пароль «12345678» принимался,
        # хотя AUTH_PASSWORD_VALIDATORS его отвергают на веб-форме.
        user = User(username=data.get('username'), email=data.get('email'))
        try:
            password_validation.validate_password(data['password'], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})

        if not data.get('accept_terms'):
            raise serializers.ValidationError({
                'accept_terms': 'Для регистрации нужно принять Пользовательское соглашение',
            })
        if not data.get('consent_data_processing'):
            raise serializers.ValidationError({
                'consent_data_processing':
                    'Без согласия на обработку персональных данных регистрация невозможна',
            })

        return data

    def create(self, validated_data):
        for field in ('password2', 'accept_terms', 'consent_data_processing',
                      'consent_distribution'):
            validated_data.pop(field, None)
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = authenticate(
            request=self.context.get('request'),
            username=data.get('username'),
            password=data.get('password'),
        )
        if user and user.is_active:
            return user
        raise serializers.ValidationError('Неверные учетные данные')


# ==================== ПОЛЬЗОВАТЕЛИ ====================

class UserSerializer(serializers.ModelSerializer):
    """
    Публичное представление пользователя.

    Ни email, ни баланс здесь больше нет: этот сериализатор используется
    в списке /api/users/ и во вложенных структурах, и раньше отдавал
    адреса и остатки всех пользователей любому авторизованному.
    """

    class Meta:
        model = User
        fields = ['id', 'username', 'date_joined']


class CurrentUserSerializer(serializers.ModelSerializer):
    """Собственный профиль: email и баланс видны только владельцу."""

    balance = serializers.DecimalField(
        source='balance.amount', max_digits=12, decimal_places=2, read_only=True,
    )
    verification_level = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'date_joined', 'balance', 'verification_level']

    def get_verification_level(self, obj):
        verification = getattr(obj, 'verification', None)
        return verification.level if verification else 'unverified'


# ==================== СБОРЫ ====================

class FundraiseListSerializer(serializers.ModelSerializer):
    progress_percent = serializers.SerializerMethodField()
    author_name = serializers.CharField(source='author.username')
    author_id = serializers.IntegerField(source='author.id')
    
    class Meta:
        model = Fundraise
        fields = ['id', 'title', 'description', 'category', 'target_amount',
                  'current_amount', 'progress_percent', 'author_name', 'author_id',
                  'created_at', 'donors_count', 'status', 'moderation_status', 'image_url']
    
    def get_progress_percent(self, obj):
        return obj.get_progress_percent()


class FundraiseDetailSerializer(serializers.ModelSerializer):
    progress_percent = serializers.SerializerMethodField()
    author = UserSerializer(read_only=True)
    is_author = serializers.SerializerMethodField()
    # Комментарий модератора адресован автору: это переписка о его заявке,
    # а не сведения о сборе для посторонних
    moderation_comment = serializers.SerializerMethodField()

    class Meta:
        model = Fundraise
        # Явный список вместо '__all__': при добавлении в модель любого
        # внутреннего поля оно автоматически попадало бы в публичный ответ.
        fields = [
            'id', 'title', 'description', 'category', 'target_amount',
            'current_amount', 'progress_percent', 'author', 'is_author',
            'created_at', 'end_date', 'status', 'image_url',
            'is_featured', 'donors_count', 'moderation_status', 'moderation_comment',
        ]
        # Результат проверки меняется только через модерацию: доступный на
        # запись moderation_status означал бы, что сбор публикует сам автор.
        read_only_fields = ['current_amount', 'donors_count', 'is_featured', 'status',
                            'moderation_status', 'moderation_comment']

    def get_progress_percent(self, obj):
        return obj.get_progress_percent()
    
    def get_is_author(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.author == request.user
        return False

    def get_moderation_comment(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user and user.is_authenticated and (obj.author_id == user.pk or user.is_staff):
            return obj.moderation_comment
        return ''


class FundraiseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fundraise
        fields = ['title', 'description', 'category', 'target_amount', 'end_date', 'image_url']
    
    def validate_target_amount(self, value):
        if value < 100:
            raise serializers.ValidationError("Минимальная сумма сбора - 100 ₽")
        if value > 10000000:
            raise serializers.ValidationError("Максимальная сумма сбора - 10 000 000 ₽")
        return value


# ==================== ДОНАТЫ ====================

class DonationSerializer(serializers.ModelSerializer):
    donor_name = serializers.SerializerMethodField()
    donor_id = serializers.SerializerMethodField()

    class Meta:
        model = Donation
        fields = ['id', 'donor_name', 'donor_id', 'amount', 'message',
                  'created_at', 'is_anonymous']

    def _is_visible(self, obj):
        """Анонимность раскрывается только самому донору."""
        if not obj.is_anonymous:
            return True
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and request.user.pk == obj.donor_id)

    def get_donor_name(self, obj):
        # Флаг is_anonymous раньше просто отдавался клиенту вместе с именем
        # донора — скрывать его должен был фронтенд, то есть не скрывал никто.
        return obj.donor.username if self._is_visible(obj) else 'Аноним'

    def get_donor_id(self, obj):
        return obj.donor_id if self._is_visible(obj) else None


class DonationCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('1.00')
    )
    message = serializers.CharField(required=False, allow_blank=True)
    is_anonymous = serializers.BooleanField(default=False)
    
    def validate_amount(self, value):
        if value > Decimal('1000000.00'):
            raise serializers.ValidationError("Максимальная сумма - 1 000 000 ₽")
        return value


# ==================== ТРАНЗАКЦИИ ====================

class TransactionSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    receiver_name = serializers.CharField(source='receiver.username', read_only=True)
    
    class Meta:
        model = Transaction
        fields = ['id', 'sender_name', 'receiver_name', 'amount', 'comment',
                  'created_at', 'status', 'is_donation']


class TransferSerializer(serializers.Serializer):
    receiver_username = serializers.CharField()
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('0.01')
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_receiver_username(self, value):
        try:
            return User.objects.get(username__iexact=value.strip())
        except User.DoesNotExist:
            raise serializers.ValidationError('Пользователь не найден')


# ==================== КОНСЕНТЫ ====================

class ConsentSerializer(serializers.ModelSerializer):
    consent_type_display = serializers.CharField(source='get_consent_type_display', read_only=True)
    
    class Meta:
        model = UserConsent
        fields = ['id', 'consent_type', 'consent_type_display', 'version',
                  'agreed_at', 'is_accepted']


# ==================== СТАТИСТИКА ====================

# Сериализатор DashboardStatsSerializer удалён: эндпоинт статистики
# собирает ответ вручную, а этот класс не использовался нигде.
