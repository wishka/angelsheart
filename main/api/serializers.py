from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from main.models import UserConsent, Balance, Transaction, Fundraise, Donation
from decimal import Decimal


# ==================== АУТЕНТИФИКАЦИЯ ====================

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password2 = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password2']
    
    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError("Пароли не совпадают")
        return data
    
    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()
    
    def validate(self, data):
        user = authenticate(**data)
        if user and user.is_active:
            return user
        raise serializers.ValidationError("Неверные учетные данные")


# ==================== ПОЛЬЗОВАТЕЛИ ====================

class UserSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(source='balance.amount', max_digits=10, decimal_places=2, read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'date_joined', 'balance']


# ==================== СБОРЫ ====================

class FundraiseListSerializer(serializers.ModelSerializer):
    progress_percent = serializers.SerializerMethodField()
    author_name = serializers.CharField(source='author.username')
    author_id = serializers.IntegerField(source='author.id')
    
    class Meta:
        model = Fundraise
        fields = ['id', 'title', 'description', 'category', 'target_amount',
                  'current_amount', 'progress_percent', 'author_name', 'author_id',
                  'created_at', 'donors_count', 'status', 'image_url']
    
    def get_progress_percent(self, obj):
        return obj.get_progress_percent()


class FundraiseDetailSerializer(serializers.ModelSerializer):
    progress_percent = serializers.SerializerMethodField()
    author = UserSerializer(read_only=True)
    is_author = serializers.SerializerMethodField()
    
    class Meta:
        model = Fundraise
        fields = '__all__'
    
    def get_progress_percent(self, obj):
        return obj.get_progress_percent()
    
    def get_is_author(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.author == request.user
        return False


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
    donor_name = serializers.CharField(source='donor.username', read_only=True)
    donor_id = serializers.IntegerField(source='donor.id', read_only=True)
    
    class Meta:
        model = Donation
        fields = ['id', 'donor_name', 'donor_id', 'amount', 'message',
                  'created_at', 'is_anonymous']


class DonationCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('1.00'))
    message = serializers.CharField(required=False, allow_blank=True)
    is_anonymous = serializers.BooleanField(default=False)
    
    def validate_amount(self, value):
        if value > 1000000:
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
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    comment = serializers.CharField(required=False, allow_blank=True)
    
    def validate_receiver_username(self, value):
        from django.contrib.auth.models import User
        try:
            user = User.objects.get(username=value)
            return user
        except User.DoesNotExist:
            raise serializers.ValidationError("Пользователь не найден")


# ==================== КОНСЕНТЫ ====================

class ConsentSerializer(serializers.ModelSerializer):
    consent_type_display = serializers.CharField(source='get_consent_type_display', read_only=True)
    
    class Meta:
        model = UserConsent
        fields = ['id', 'consent_type', 'consent_type_display', 'version',
                  'agreed_at', 'is_accepted']


# ==================== СТАТИСТИКА ====================

class DashboardStatsSerializer(serializers.Serializer):
    balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_sent = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_received = serializers.DecimalField(max_digits=12, decimal_places=2)
    recent_transactions = TransactionSerializer(many=True)