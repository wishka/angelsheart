from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Balance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='balance')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    def __str__(self):
        return f"{self.user.username} - {self.amount} ₽"


class Transaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('completed', 'Завершена'),
        ('failed', 'Ошибка'),
    ]
    
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_transactions')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_transactions')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    created_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True, null=True)
    is_donation = models.BooleanField(default=False)  # Для пожертвований в сборы
    fundraise_id = models.IntegerField(null=True, blank=True)  # ID сбора
    
    def __str__(self):
        return f"{self.sender} -> {self.receiver}: {self.amount}"


class UserConsent(models.Model):
    """Модель для хранения согласий пользователя"""
    
    CONSENT_TYPES = [
        ('privacy', 'Политика конфиденциальности'),
        ('terms', 'Пользовательское соглашение'),
        ('cookies', 'Политика cookies'),
        ('marketing', 'Маркетинговые уведомления'),
        ('data_processing', 'Обработка персональных данных'),
    ]
    
    CONSENT_VERSION_CHOICES = [
        ('1.0', 'Версия 1.0'),
        ('1.1', 'Версия 1.1'),
        ('2.0', 'Версия 2.0'),
    ]
    
    # Связь с пользователем
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='consents',
        verbose_name='Пользователь'
    )
    
    # Тип согласия
    consent_type = models.CharField(
        max_length=20,
        choices=CONSENT_TYPES,
        verbose_name='Тип согласия'
    )
    
    # Версия документа
    version = models.CharField(
        max_length=10,
        choices=CONSENT_VERSION_CHOICES,
        default='1.0',
        verbose_name='Версия'
    )
    
    # Статус согласия
    is_accepted = models.BooleanField(
        default=False,
        verbose_name='Согласие принято'
    )
    
    # Дата согласия
    agreed_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Дата согласия'
    )
    
    # IP-адрес при согласии
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP-адрес'
    )
    
    # User-Agent браузера
    user_agent = models.TextField(
        blank=True,
        null=True,
        verbose_name='User-Agent'
    )
    
    # Дата отзыва согласия (если применимо)
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата отзыва'
    )
    
    # Причина отзыва (если применимо)
    revocation_reason = models.TextField(
        blank=True,
        null=True,
        verbose_name='Причина отзыва'
    )
    
    class Meta:
        verbose_name = 'Согласие пользователя'
        verbose_name_plural = 'Согласия пользователей'
        ordering = ['-agreed_at']
        # Уникальность: один пользователь - один тип согласия - одна версия
        unique_together = ['user', 'consent_type', 'version']
    
    def __str__(self):
        status = "✅ Принято" if self.is_accepted else "❌ Не принято"
        return f"{self.user.username} - {self.get_consent_type_display()} - {status}"
    
    def revoke(self, reason=None):
        """Метод для отзыва согласия"""
        self.is_accepted = False
        self.revoked_at = timezone.now()
        self.revocation_reason = reason
        self.save()
    
    @classmethod
    def has_active_consent(cls, user, consent_type, version='1.0'):
        """Проверка наличия активного согласия"""
        return cls.objects.filter(
            user=user,
            consent_type=consent_type,
            version=version,
            is_accepted=True,
            revoked_at__isnull=True
        ).exists()
    
    @classmethod
    def get_user_consents(cls, user):
        """Получить все согласия пользователя"""
        return cls.objects.filter(user=user, is_accepted=True, revoked_at__isnull=True)


class ConsentLog(models.Model):
    """Лог изменений согласий для аудита"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='consent_logs')
    action = models.CharField(max_length=20, choices=[('accept', 'Принятие'), ('revoke', 'Отзыв')])
    consent_type = models.CharField(max_length=20, choices=UserConsent.CONSENT_TYPES)
    version = models.CharField(max_length=10)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Лог согласий'
        verbose_name_plural = 'Логи согласий'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.get_consent_type_display()} - {self.created_at}"

class Fundraise(models.Model):
    """Модель для сбора средств"""
    CATEGORY_CHOICES = [
        ('medical', 'Медицина и здоровье'),
        ('education', 'Образование'),
        ('animal', 'Помощь животным'),
        ('ecology', 'Экология'),
        ('sport', 'Спорт'),
        ('art', 'Искусство и культура'),
        ('business', 'Бизнес и стартапы'),
        ('other', 'Другое'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Активный'),
        ('completed', 'Завершен'),
        ('cancelled', 'Отменен'),
    ]
    
    title = models.CharField(max_length=200, verbose_name='Название сбора')
    description = models.TextField(verbose_name='Описание цели')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other', verbose_name='Категория')
    target_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Целевая сумма')
    current_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Собрано')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fundraises', verbose_name='Автор')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    end_date = models.DateTimeField(null=True, blank=True, verbose_name='Дата окончания')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name='Статус')
    image_url = models.URLField(blank=True, null=True, verbose_name='Ссылка на изображение')
    is_featured = models.BooleanField(default=False, verbose_name='Рекомендуемый')
    donors_count = models.IntegerField(default=0, verbose_name='Количество донатеров')
    
    total_commission = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0.00,
        verbose_name='Всего комиссии'
    )
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Сбор средств'
        verbose_name_plural = 'Сборы средств'
    
    def __str__(self):
        return f"{self.title} - {self.current_amount}/{self.target_amount} ₽"
    
    def get_progress_percent(self):
        """Возвращает процент выполнения"""
        if self.target_amount > 0:
            return int((self.current_amount / self.target_amount) * 100)
        return 0
    
    def is_completed(self):
        """Проверяет, достигнута ли цель"""
        return self.current_amount >= self.target_amount


class Donation(models.Model):
    """Модель для пожертвований в сбор"""
    donor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='donations', verbose_name='Донатер')
    fundraise = models.ForeignKey(Fundraise, on_delete=models.CASCADE, related_name='donations', verbose_name='Сбор')
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Сумма')
    message = models.TextField(blank=True, null=True, verbose_name='Сообщение поддержки')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    is_anonymous = models.BooleanField(default=False, verbose_name='Анонимно')
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.donor.username} -> {self.fundraise.title}: {self.amount} ₽"


@receiver(post_save, sender=User)
def create_user_balance(sender, instance, created, **kwargs):
    if created:
        Balance.objects.create(user=instance)


class CommissionTransaction(models.Model):
    """Модель для учета комиссионных сборов"""
    donation = models.OneToOneField(Donation, on_delete=models.CASCADE, related_name='commission')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    percent = models.IntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Комиссия {self.percent}% с пожертвования #{self.donation.id}: {self.amount} ₽"


class WithdrawalRequest(models.Model):
    """Модель для заявок на вывод средств"""
    
    STATUS_CHOICES = [
        ('pending', 'На рассмотрении'),
        ('processing', 'В обработке'),
        ('completed', 'Выполнен'),
        ('rejected', 'Отклонен'),
        ('cancelled', 'Отменен пользователем'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('card', 'Банковская карта'),
        ('sbp', 'СБП'),
        ('yoomoney', 'ЮMoney'),
        ('crypto', 'Криптовалюта'),
    ]
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='withdrawal_requests',
        verbose_name='Пользователь'
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Сумма вывода'
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        verbose_name='Способ вывода'
    )
    payment_details = models.JSONField(
        verbose_name='Реквизиты для вывода',
        help_text='Данные для перевода (карта, номер телефона, кошелек и т.д.)'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Статус'
    )
    comment = models.TextField(
        blank=True,
        null=True,
        verbose_name='Комментарий администратора'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Дата создания'
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата обработки'
    )
    processed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_withdrawals',
        verbose_name='Обработал'
    )
    transaction_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='ID транзакции в платежной системе'
    )
    
    class Meta:
        verbose_name = 'Заявка на вывод'
        verbose_name_plural = 'Заявки на вывод'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.id} - {self.user.username} - {self.amount} ₽ - {self.get_status_display()}"
    
    def approve(self, admin_user, transaction_id=None):
        """Подтверждение заявки администратором"""
        from django.utils import timezone
        self.status = 'processing'
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        self.transaction_id = transaction_id
        self.save()
        
        # Здесь должна быть интеграция с платежной системой
        # Для реального вывода средств
    
    def complete(self, admin_user, transaction_id=None):
        """Завершение выплаты"""
        from django.utils import timezone
        self.status = 'completed'
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        if transaction_id:
            self.transaction_id = transaction_id
        self.save()
    
    def reject(self, admin_user, reason):
        """Отклонение заявки"""
        from django.utils import timezone
        self.status = 'rejected'
        self.comment = reason
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        self.save()
        
        # Возвращаем средства пользователю
        balance = Balance.objects.get(user=self.user)
        balance.amount += self.amount
        balance.save()
    
    def cancel(self):
        """Отмена заявки пользователем"""
        if self.status == 'pending':
            self.status = 'cancelled'
            self.save()
            # Возвращаем средства
            balance = Balance.objects.get(user=self.user)
            balance.amount += self.amount
            balance.save()
            return True
        return False