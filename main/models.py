import os
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from main.storage import private_media_storage
from main.utils.fields import EncryptedJSONField, EncryptedTextField


class Balance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='balance')
    # max_digits=12: прежние 10 знаков давали потолок 99 999 999.99 ₽,
    # при превышении которого запись ломалась на уровне СУБД.
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        constraints = [
            # Последний рубеж против ухода баланса в минус: даже если
            # проверка в коде будет пропущена, СУБД не даст записать долг.
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='balance_amount_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.amount} ₽"


class Transaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('completed', 'Завершена'),
        ('failed', 'Ошибка'),
    ]

    KIND_CHOICES = [
        ('transfer', 'Перевод пользователю'),
        ('donation', 'Пожертвование в сбор'),
        ('topup', 'Пополнение баланса'),
        ('refund', 'Возврат'),
        ('withdrawal', 'Вывод средств'),
    ]

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_transactions')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_transactions')
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    created_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True, null=True)
    is_donation = models.BooleanField(default=False)  # Для пожертвований в сборы
    # Пополнение раньше записывалось как перевод самому себе и попадало
    # одновременно в «отправлено» и «получено», задваивая обороты в рейтинге.
    # kind позволяет отличать типы операций в выборках и статистике.
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='transfer', db_index=True)
    fundraise_id = models.IntegerField(null=True, blank=True)  # ID сбора

    class Meta:
        indexes = [
            models.Index(fields=['sender', '-created_at']),
            models.Index(fields=['receiver', '-created_at']),
        ]

    def __str__(self):
        return f"{self.sender} -> {self.receiver}: {self.amount}"


class UserConsent(models.Model):
    """Модель для хранения согласий пользователя"""
    
    CONSENT_TYPES = [
        ('terms', 'Пользовательское соглашение (акцепт оферты)'),
        ('data_processing', 'Обработка персональных данных (ст. 9 152-ФЗ)'),
        # Отдельное согласие для публикации сбора: ч. 1 ст. 10.1 152-ФЗ
        # требует оформлять его отдельно от прочих согласий
        ('distribution', 'Распространение персональных данных (ст. 10.1 152-ФЗ)'),
        ('cookies', 'Политика cookies'),
        ('marketing', 'Маркетинговые уведомления'),
        # Оставлено для совместимости с записями, созданными до разделения
        # согласий: раньше акцепт политики учитывался этим типом
        ('privacy', 'Политика конфиденциальности (устаревший тип)'),
    ]

    # Согласия, без которых сервис не может оказывать услугу
    REQUIRED_TYPES = ('terms', 'data_processing')
    
    # Список версий больше не фиксируется в choices: версия приходит из
    # settings.LEGAL_DOCS_VERSION, а жёсткий перечень означал бы запись мимо choices
    # при первом же обновлении редакции документов.

    # Версия действующей редакции документов. Берётся из настроек, чтобы
    # текст документа и записанное согласие не разошлись: раньше версия
    # была записана строкой '1.0' в пяти местах кода.
    @classmethod
    def current_version(cls):
        from django.conf import settings
        return settings.LEGAL_DOCS_VERSION


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
    def has_active_consent(cls, user, consent_type, version=None):
        """
        Проверка наличия активного согласия.

        По умолчанию проверяется действующая редакция документов: раньше
        здесь была зашита строка '1.0', и после обновления редакции
        проверка молча перестала бы находить согласия.
        """
        return cls.objects.filter(
            user=user,
            consent_type=consent_type,
            version=version or cls.current_version(),
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
        ('draft', 'Черновик'),
        ('active', 'Активный'),
        ('completed', 'Завершен'),
        ('cancelled', 'Отменен'),
    ]

    # Модерация отделена от жизненного цикла: сбор может быть «активным»
    # по замыслу автора и при этом не прошедшим проверку. Раньше модерации
    # не было вовсе — любой мгновенно публиковал сбор на 10 млн «на лечение»
    # без единого документа, и площадка отвечала за это по ст. 159 УК.
    MODERATION_CHOICES = [
        ('draft', 'Не отправлен на проверку'),
        ('pending', 'На проверке'),
        ('approved', 'Одобрен'),
        ('changes_requested', 'Требуются уточнения'),
        ('rejected', 'Отклонён'),
    ]

    # Изменение любого из этих полей после одобрения возвращает сбор
    # на повторную проверку: иначе модерацию можно пройти безобидным
    # текстом и подменить его сразу после одобрения.
    MODERATED_FIELDS = ('title', 'description', 'category', 'target_amount', 'image_url')

    # Статусы, при которых сбор считается незавершённым: автор не может
    # завести второй, пока не довёл до конца первый.
    UNFINISHED_STATUSES = ('draft', 'active')

    title = models.CharField(max_length=200, verbose_name='Название сбора')
    description = models.TextField(verbose_name='Описание цели')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other', verbose_name='Категория')
    target_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Целевая сумма')
    # default=Decimal, а не 0.00: с float-значением по умолчанию свежий
    # объект в памяти хранил float, и первое же деление на Decimal
    # (расчёт процента выполнения) падало с TypeError — например,
    # сразу после создания сбора через API.
    current_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='Собрано',
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fundraises', verbose_name='Автор')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    end_date = models.DateTimeField(null=True, blank=True, verbose_name='Дата окончания')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='Статус')
    image_url = models.URLField(blank=True, null=True, verbose_name='Ссылка на изображение')
    is_featured = models.BooleanField(default=False, verbose_name='Рекомендуемый')
    donors_count = models.IntegerField(default=0, verbose_name='Количество донатеров')

    # ===== Модерация =====
    moderation_status = models.CharField(
        max_length=20, choices=MODERATION_CHOICES, default='draft',
        db_index=True, verbose_name='Статус проверки',
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Отправлен на проверку')
    moderated_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата проверки')
    moderated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='moderated_fundraises', verbose_name='Проверил',
    )
    moderation_comment = models.TextField(
        blank=True, default='', verbose_name='Комментарий модератора',
        help_text='Показывается автору сбора',
    )
    # Момент закрытия сбора. Нужен для сроков хранения: Политика обещает
    # удалять приложенные документы через N дней после завершения или отмены,
    # а без этой отметки отсчитывать было не от чего.
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Закрыт')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Сбор средств'
        verbose_name_plural = 'Сборы средств'
        indexes = [models.Index(fields=['moderation_status', 'status'])]

    def save(self, *args, **kwargs):
        # Отметка о закрытии ставится здесь, а не в каждом месте, где сбор
        # закрывается: путей несколько (вью автора, модерация, админка),
        # и любой забытый оставил бы документы без срока хранения.
        if self.status in ('completed', 'cancelled') and self.closed_at is None:
            self.closed_at = timezone.now()
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'closed_at' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['closed_at']
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} - {self.current_amount}/{self.target_amount} ₽"

    @classmethod
    def published(cls):
        """
        Сборы, видимые посторонним и принимающие пожертвования.

        Единственное место, задающее это условие: раньше списки фильтровали
        по status='active', и непроверенный сбор попадал в выдачу.
        """
        # Сборы пользователя с действующим ограничением с витрины снимаются:
        # ограничение вводится при подозрении, а витрина — это рекомендация
        # площадки, за которую она отвечает.
        #
        # Исключение идёт через явный подзапрос, а не через
        # exclude(author__restrictions__lifted_at__isnull=True): последнее
        # из-за LEFT JOIN отбрасывает и авторов вообще без ограничений.
        restricted_authors = AccountRestriction.objects.filter(
            lifted_at__isnull=True,
        ).values('user_id')

        return (
            cls.objects.filter(moderation_status='approved')
            .exclude(status='draft')
            .exclude(author_id__in=restricted_authors)
        )

    @property
    def is_public(self):
        """
        Виден ли сбор посторонним.

        Условие совпадает с published(), включая ограничение автора: иначе
        сбор уходил с витрины, но оставался доступен по прямой ссылке —
        а рассылает такую ссылку как раз автор.
        """
        if self.moderation_status != 'approved' or self.status == 'draft':
            return False
        return AccountRestriction.active_for(self.author) is None

    @property
    def accepts_donations(self):
        """
        Принимает ли сбор деньги прямо сейчас.

        Срок окончания проверяется здесь, а не только командой
        close_expired_fundraises: команда ходит раз в час, и в промежутке
        сбор с истёкшим сроком продолжал бы принимать пожертвования.
        Жертвователь видит на странице дату окончания и считает её
        условием сбора.
        """
        if self.moderation_status != 'approved' or self.status != 'active':
            return False
        if self.end_date and self.end_date < timezone.now():
            return False
        return True

    @property
    def is_expired(self):
        return bool(self.end_date and self.end_date < timezone.now() and self.status == 'active')

    @property
    def is_editable(self):
        """Автор может править сбор во всех состояниях, кроме ожидания решения."""
        return self.moderation_status != 'pending' and self.status not in ('completed', 'cancelled')

    @property
    def moderation_hint(self):
        """Что происходит со сбором — одной фразой для автора."""
        if self.status == 'cancelled':
            return 'Сбор отменён.'
        if self.status == 'completed':
            return 'Сбор завершён.'
        return {
            'draft': 'Черновик. Отправьте сбор на проверку, чтобы опубликовать его.',
            'pending': 'На проверке у модератора.',
            'approved': 'Опубликован.',
            'changes_requested': 'Модератор попросил уточнения — исправьте и отправьте снова.',
            'rejected': 'Отклонён модератором.',
        }.get(self.moderation_status, '')

    def get_progress_percent(self):
        """Процент выполнения сбора."""
        if not self.target_amount:
            return 0
        # Приведение к Decimal защищает от смешения типов: значения могут
        # прийти из формы, из сериализатора или из значения по умолчанию
        current = Decimal(str(self.current_amount or 0))
        target = Decimal(str(self.target_amount))
        if target <= 0:
            return 0
        return int((current / target) * 100)

    def is_completed(self):
        """Проверяет, достигнута ли цель"""
        return self.current_amount >= self.target_amount

    def submit_for_moderation(self):
        """
        Отправка сбора на проверку автором.

        Завершённый и отменённый сбор отправить нельзя: иначе повторная
        подача снимала сбор с публикации, а последующее одобрение
        воскрешало его — вместе с деньгами, уже возвращёнными донорам.
        """
        from django.utils import timezone

        if self.moderation_status == 'pending':
            return False
        if self.status in ('completed', 'cancelled'):
            return False
        self.moderation_status = 'pending'
        self.submitted_at = timezone.now()
        self.moderation_comment = ''
        self.save(update_fields=['moderation_status', 'submitted_at', 'moderation_comment'])
        return True

    def reset_moderation(self):
        """
        Снятие одобрения после правки проверенных сведений.

        Сбор снимается с витрины и перестаёт принимать деньги, но `status`
        не трогается. Это важно: перевод в 'draft' запирал бы сбор, где уже
        есть пожертвования, — отменить его (и вернуть деньги) или завершить
        стало бы нельзя, потому что оба действия требуют status='active'.
        Собранные средства остаются у сбора, и автор по-прежнему может его
        отменить с возвратом.
        """
        self.moderation_status = 'draft'
        self.moderated_at = None
        self.moderated_by = None
        self.submitted_at = None
        self.moderation_comment = ''
        self.save(update_fields=[
            'moderation_status', 'moderated_at', 'moderated_by',
            'submitted_at', 'moderation_comment',
        ])

    def approve(self, moderator, comment=''):
        """
        Одобрение: сбор становится публичным и начинает принимать средства.

        Завершённый и отменённый сбор одобрение не оживляет: status меняется
        на 'active' только у сбора, который ещё не закрыт. Иначе одобрение
        заявки, поданной до отмены, вернуло бы на витрину сбор, деньги по
        которому уже возвращены донорам.
        """
        from django.utils import timezone

        self.moderation_status = 'approved'
        if self.status not in ('completed', 'cancelled'):
            self.status = 'active'
        self.moderated_at = timezone.now()
        self.moderated_by = moderator
        self.moderation_comment = comment
        self.save(update_fields=[
            'moderation_status', 'status', 'moderated_at', 'moderated_by', 'moderation_comment',
        ])

    def request_changes(self, moderator, comment):
        """
        Возврат автору на доработку.

        `status` не трогается по той же причине, что и в reset_moderation():
        перевод в 'draft' запирал бы сбор, где уже есть пожертвования, —
        ни отменить с возвратом, ни завершить его стало бы нельзя.
        Публикацию снимает moderation_status, этого достаточно.
        """
        from django.utils import timezone

        self.moderation_status = 'changes_requested'
        self.moderated_at = timezone.now()
        self.moderated_by = moderator
        self.moderation_comment = comment
        self.save(update_fields=[
            'moderation_status', 'moderated_at', 'moderated_by', 'moderation_comment',
        ])

    def reject(self, moderator, comment):
        """Отклонение сбора. Собранные средства возвращаются отдельно."""
        from django.utils import timezone

        self.moderation_status = 'rejected'
        self.status = 'cancelled'
        self.moderated_at = timezone.now()
        self.moderated_by = moderator
        self.moderation_comment = comment
        self.save(update_fields=[
            'moderation_status', 'status', 'moderated_at', 'moderated_by', 'moderation_comment',
        ])


class FundraiseDocument(models.Model):
    """
    Документ, подтверждающий цель сбора.

    Для сборов на лечение и подобных категорий одних слов автора мало:
    без подтверждения площадка публикует непроверенное утверждение о
    нуждаемости и принимает за него деньги.

    Хранится в приватном хранилище: выписка или справка содержит сведения
    о здоровье — специальную категорию ПДн (ст. 10 152-ФЗ).
    """

    DOCUMENT_TYPES = [
        ('medical', 'Медицинский документ (выписка, направление, счёт)'),
        ('invoice', 'Счёт или смета'),
        ('official', 'Официальный документ (справка, решение)'),
        ('other', 'Иное подтверждение'),
    ]

    fundraise = models.ForeignKey(
        Fundraise, on_delete=models.CASCADE, related_name='documents',
        verbose_name='Сбор',
    )
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, verbose_name='Тип')
    file = models.FileField(
        upload_to='fundraise-docs/%Y/%m/', storage=private_media_storage,
        verbose_name='Файл',
    )
    comment = models.TextField(blank=True, default='', verbose_name='Пояснение автора')
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='Загружен')

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Документ к сбору'
        verbose_name_plural = 'Документы к сборам'

    def __str__(self):
        return f'{self.get_document_type_display()} к сбору #{self.fundraise_id}'

    @property
    def display_name(self):
        """Имя файла при скачивании — без внутреннего пути хранилища."""
        return os.path.basename(self.file.name) or f'document-{self.pk}'

    def __str__(self):
        return f'{self.get_document_type_display()} к сбору #{self.fundraise_id}'


class Donation(models.Model):
    """Модель для пожертвований в сбор"""
    donor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='donations', verbose_name='Донатер')
    fundraise = models.ForeignKey(Fundraise, on_delete=models.CASCADE, related_name='donations', verbose_name='Сбор')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    message = models.TextField(blank=True, null=True, verbose_name='Сообщение поддержки')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата')
    is_anonymous = models.BooleanField(default=False, verbose_name='Анонимно')
    # Возврат при отмене сбора: раньше отмена оставляла деньги у автора
    refunded_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата возврата')
    refunded_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name='Возвращено',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['fundraise', '-created_at'])]

    def __str__(self):
        return f"{self.donor.username} -> {self.fundraise.title}: {self.amount} ₽"


@receiver(post_save, sender=User)
def create_user_balance(sender, instance, created, **kwargs):
    if created:
        # get_or_create вместо create: раньше на User висели два сигнала,
        # и второй падал на IntegrityError, если первый уже создал баланс.
        Balance.objects.get_or_create(user=instance)


class CommissionTransaction(models.Model):
    """
    Удержанная комиссия сервиса.

    Раньше модель существовала, но не использовалась: API вычитал 3 %
    и деньги просто исчезали из системы, нигде не учитываясь.
    Теперь каждая удержанная копейка имеет запись.
    """
    donation = models.OneToOneField(Donation, on_delete=models.CASCADE, related_name='commission')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Комиссия сервиса'
        verbose_name_plural = 'Комиссии сервиса'

    def __str__(self):
        return f"Комиссия {self.percent}% с пожертвования #{self.donation_id}: {self.amount} ₽"


class WithdrawalRequest(models.Model):
    """Модель для заявок на вывод средств"""

    STATUS_CHOICES = [
        ('pending', 'На рассмотрении'),
        ('processing', 'В обработке'),
        ('completed', 'Выполнен'),
        ('rejected', 'Отклонен'),
        ('cancelled', 'Отменен пользователем'),
        # Раньше код выставлял 'failed' при ошибке выплаты, хотя такого
        # варианта не было в choices, и средства пользователю не возвращались.
        ('failed', 'Ошибка выплаты'),
    ]

    # Статусы, при которых деньги списаны с баланса и удерживаются заявкой
    HOLDING_STATUSES = ('pending', 'processing')
    # Статусы, в которых заявка завершена и повторный возврат недопустим
    FINAL_STATUSES = ('completed', 'rejected', 'cancelled', 'failed')

    PAYMENT_METHOD_CHOICES = [
        ('card', 'Банковская карта'),
        ('sbp', 'СБП'),
        ('yoomoney', 'ЮMoney'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='withdrawal_requests',
        verbose_name='Пользователь'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Сумма вывода'
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        verbose_name='Способ вывода'
    )
    # Было JSONField с номером карты открытым текстом — хранение PAN в таком
    # виде нарушает PCI DSS. Теперь содержимое шифруется Fernet.
    payment_details = EncryptedJSONField(
        blank=True,
        default=dict,
        verbose_name='Реквизиты для вывода',
        help_text='Данные для перевода (хранятся в зашифрованном виде)'
    )
    # Незашифрованная часть — только для показа в списках и поиска
    payment_details_masked = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Реквизиты (маскированные)'
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
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f"#{self.id} - {self.user.username} - {self.amount} ₽ - {self.get_status_display()}"

    def _refund(self, reason):
        """
        Возврат удержанной суммы на баланс.

        Раньше возврат делался без транзакции и без блокировки строки, из-за
        чего параллельные отмена и отклонение могли зачислить деньги дважды.
        Теперь возврат идёт под блокировкой самой заявки: повторный вызов
        для уже завершённой заявки ничего не делает.
        """
        from django.db import transaction as db_transaction

        with db_transaction.atomic():
            locked = WithdrawalRequest.objects.select_for_update().get(pk=self.pk)
            if locked.status not in self.HOLDING_STATUSES:
                return False

            balance = Balance.objects.select_for_update().get(user=locked.user)
            balance.amount += locked.amount
            balance.save(update_fields=['amount'])

            Transaction.objects.create(
                sender=locked.user,
                receiver=locked.user,
                amount=locked.amount,
                comment=f'Возврат по заявке на вывод #{locked.pk}: {reason}',
                status='completed',
                kind='refund',
            )
            return True

    def approve(self, admin_user, transaction_id=None):
        """
        Заявка принята к выплате.

        Переводит заявку в 'processing'. Сама выплата выполняется
        MassWithdrawalService — статус 'processing' означает «деньги ещё
        не отправлены», поэтому сумма остаётся удержанной.
        """
        if self.status != 'pending':
            return False
        self.status = 'processing'
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        if transaction_id:
            self.transaction_id = transaction_id
        self.save(update_fields=['status', 'processed_at', 'processed_by', 'transaction_id'])
        return True

    def complete(self, admin_user, transaction_id=None):
        """
        Выплата фактически произведена: удержанная сумма списывается окончательно.

        Реквизиты после выплаты не нужны и стираются — остаётся только маска.
        Хранить номер карты дальше значило бы держать носитель утечки без
        всякой цели, вопреки п. 7 ч. 1 ст. 5 152-ФЗ и Политике (раздел 6).
        """
        if self.status not in self.HOLDING_STATUSES:
            return False
        self.status = 'completed'
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        if transaction_id:
            self.transaction_id = transaction_id
        self.payment_details = {}
        self.save(update_fields=[
            'status', 'processed_at', 'processed_by', 'transaction_id', 'payment_details',
        ])
        return True

    def reject(self, admin_user, reason):
        """Отклонение заявки администратором с возвратом средств."""
        if not self._refund(f'отклонено администратором — {reason}'):
            return False
        self.status = 'rejected'
        self.comment = reason
        self.processed_at = timezone.now()
        self.processed_by = admin_user
        self.save(update_fields=['status', 'comment', 'processed_at', 'processed_by'])
        return True

    def mark_failed(self, reason):
        """Техническая ошибка выплаты: средства обязаны вернуться пользователю."""
        if not self._refund(f'ошибка выплаты — {reason}'):
            return False
        self.status = 'failed'
        self.comment = reason
        self.processed_at = timezone.now()
        self.save(update_fields=['status', 'comment', 'processed_at'])
        return True

    def cancel(self):
        """Отмена заявки пользователем."""
        if self.status != 'pending':
            return False
        if not self._refund('отменено пользователем'):
            return False
        self.status = 'cancelled'
        self.save(update_fields=['status'])
        return True


# ==================== ПЛАТЕЖИ И БЕЗОПАСНОСТЬ ====================

class PaymentTransaction(models.Model):
    """Модель для отслеживания платежей"""
    STATUS_CHOICES = [
        ('pending', 'Ожидает оплаты'),
        ('processing', 'В обработке'),
        ('paid', 'Оплачен'),
        ('failed', 'Ошибка'),
        ('refunded', 'Возвращен'),
        ('cancelled', 'Отменен'),
    ]
    
    METHOD_CHOICES = [
        ('card', 'Банковская карта'),
        ('sbp', 'СБП'),
        ('yoomoney', 'ЮMoney'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    payment_id = models.CharField(max_length=100, unique=True)  # ID в платежной системе
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.payment_id} - {self.user.username} - {self.amount}₽"


class TwoFactorAuth(models.Model):
    """Модель для двухфакторной аутентификации"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='two_factor_auth')
    secret_key = models.CharField(max_length=32)
    is_enabled = models.BooleanField(default=False)
    backup_codes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"2FA {'включена' if self.is_enabled else 'выключена'} для {self.user.username}"


class KYCDocument(models.Model):
    """Модель для хранения документов верификации"""
    DOCUMENT_TYPES = [
        ('passport', 'Паспорт РФ'),
        ('driver_license', 'Водительское удостоверение'),
        ('snils', 'СНИЛС'),
        ('inn', 'ИНН'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'На проверке'),
        ('approved', 'Подтвержден'),
        ('rejected', 'Отклонен'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='kyc_documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    document_number = EncryptedTextField(blank=True, default='')
    # Хранилище вне MEDIA_ROOT: сканы документов не должны отдаваться
    # веб-сервером по прямой ссылке. Выдача — через main:kyc_document.
    document_image = models.FileField(
        upload_to='kyc/%Y/%m/%d/',
        storage=private_media_storage,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    verification_comment = models.TextField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='verified_documents')
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.get_document_type_display()} - {self.get_status_display()}"


class UserVerification(models.Model):
    """Модель для статуса верификации пользователя"""
    VERIFICATION_LEVELS = [
        ('unverified', 'Не верифицирован'),
        ('basic', 'Базовая верификация'),
        ('full', 'Полная верификация'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='verification')
    level = models.CharField(max_length=20, choices=VERIFICATION_LEVELS, default='unverified')
    full_name = models.CharField(max_length=200, blank=True, null=True)
    birth_date = models.DateField(null=True, blank=True)
    # Паспортные данные лежали в базе открытым текстом. Теперь шифруются;
    # как следствие, поиск по ним в админке невозможен и убран.
    passport_series = EncryptedTextField(blank=True, default='')
    passport_number = EncryptedTextField(blank=True, default='')
    address = EncryptedTextField(blank=True, default='')
    verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Раньше пользователь сам вводил паспорт и мгновенно получал уровень
    # basic. Теперь уровень повышает только администратор после проверки
    # документов, а это поле хранит, что данные заявлены и ждут проверки.
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Данные поданы')

    @property
    def passport_masked(self):
        """Маскированное представление для показа в интерфейсе и админке."""
        series = self.passport_series or ''
        number = self.passport_number or ''
        if not series and not number:
            return '—'
        return f'{series[:2]}** ***{number[-3:]}' if number else f'{series[:2]}**'

    def __str__(self):
        return f"{self.user.username} - {self.get_level_display()}"


class SecurityLog(models.Model):
    """Модель для логирования безопасности"""
    ACTION_CHOICES = [
        ('login', 'Вход'),
        ('logout', 'Выход'),
        ('failed_login', 'Неудачный вход'),
        ('2fa_enabled', 'Включена 2FA'),
        ('2fa_disabled', 'Выключена 2FA'),
        ('password_change', 'Смена пароля'),
        ('withdrawal', 'Вывод средств'),
        ('payment', 'Платеж'),
        ('verification', 'Верификация'),
        ('suspicious', 'Подозрительная активность'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='security_logs', null=True, blank=True)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    # username хранится отдельно: при неудачном входе пользователя может
    # не существовать, а расследовать перебор по несуществующим логинам нужно.
    username_attempted = models.CharField(max_length=150, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', '-created_at']),
            models.Index(fields=['ip_address', '-created_at']),
        ]

    def __str__(self):
        return f"{self.created_at} - {self.user} - {self.get_action_display()}"


class PersonalDataAccessLog(models.Model):
    """
    Журнал обращений сотрудников к персональным данным пользователей.

    Политика конфиденциальности обещает, что доступ сотрудников к ПДн
    журналируется. До этой модели обещание не выполнялось: Django пишет
    в LogEntry только изменения объектов, но не просмотры, — а именно
    просмотр паспорта или медицинской справки и есть то обращение,
    которое нужно уметь восстановить.

    Требование к учёту лиц, имеющих доступ, и к фиксации самого факта
    доступа — ч. 1 ст. 19 152-ФЗ и п. 15 Требований, утв. постановлением
    Правительства РФ № 1119. Практическая сторона важнее формальной:
    без журнала утечку невозможно ни расследовать, ни ограничить, и
    уведомление в РКН по ст. 21 152-ФЗ нечем наполнить.
    """

    DATA_TYPES = [
        ('passport', 'Паспортные данные'),
        ('kyc_document', 'Скан документа, удостоверяющего личность'),
        ('fundraise_document', 'Документ к сбору (в т. ч. сведения о здоровье)'),
        ('payment_details', 'Платёжные реквизиты'),
        ('contact', 'Контактные данные'),
        ('export', 'Полная выгрузка данных пользователя'),
        ('other', 'Иное'),
    ]

    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='pd_accesses', verbose_name='Кто обратился',
    )
    subject = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='pd_accessed', verbose_name='Чьи данные',
    )
    data_type = models.CharField(max_length=32, choices=DATA_TYPES, verbose_name='Категория данных')
    reason = models.CharField(max_length=255, blank=True, default='', verbose_name='Основание')
    object_repr = models.CharField(max_length=255, blank=True, default='', verbose_name='Объект')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP')
    user_agent = models.TextField(blank=True, default='', verbose_name='User-Agent')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Когда')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Обращение к персональным данным'
        verbose_name_plural = 'Журнал доступа к персональным данным'
        indexes = [
            models.Index(fields=['subject', '-created_at']),
            models.Index(fields=['actor', '-created_at']),
        ]

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.actor} → {self.subject} ({self.data_type})'

    @classmethod
    def record(cls, actor, subject, data_type, reason='', request=None, object_repr=''):
        """
        Запись обращения.

        Журналирование не должно ронять основное действие: если запись
        не удалась, сотрудник всё равно получает документ, а сбой уходит
        в лог. Обратный порядок означал бы, что отказ журнала блокирует
        проверку сбора.
        """
        import logging

        ip = user_agent = None
        if request is not None:
            from main.utils.request_meta import get_request_meta

            ip, user_agent = get_request_meta(request)

        try:
            return cls.objects.create(
                actor=actor if getattr(actor, 'pk', None) else None,
                subject=subject if getattr(subject, 'pk', None) else None,
                data_type=data_type,
                reason=reason[:255],
                object_repr=object_repr[:255],
                ip_address=ip,
                user_agent=user_agent or '',
            )
        except Exception:
            logging.getLogger('security').exception(
                'Не удалось записать обращение к ПДн: %s → %s (%s)',
                actor, subject, data_type,
            )
            return None


# Криптовалютные модели (CryptoBalance, CryptoTransaction) удалены.
# Причина не техническая: приём цифровой валюты как встречного предоставления
# за товары, работы и услуги в РФ запрещён, и запрет сохранён в 282-ФЗ,
# действующем с 01.09.2026. Код всё равно был нерабочим — оба провайдера
# не реализовывали абстрактные методы базового класса.

def add_business_days(moment, days):
    """
    Прибавление рабочих дней.

    Государственные праздники не учитываются: производственный календарь
    меняется ежегодно и его пришлось бы поддерживать вручную. Их отсутствие
    работает против Оператора — срок наступает раньше, чем если бы праздники
    считались нерабочими, и нарушить его легче.

    Выходные при этом срок сдвигают: пятница + 1 рабочий день = понедельник.
    Так написано в оферте («рабочих дней»), и иначе считать нельзя, но стоит
    понимать, что для пользователя это означает ожидание длиннее календарного.
    """
    result = moment
    added = 0
    while added < days:
        result += timezone.timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


class AccountRestriction(models.Model):
    """
    Приостановление операций и блокировка учётной записи.

    Раздел 9 оферты описывает эту процедуру со сроками — уведомление
    в течение 1 рабочего дня, рассмотрение возражений в течение 5 рабочих
    дней, — но в коде её не было вовсе: заблокировать пользователя можно
    было только через is_active=False, без причины, без уведомления и без
    возможности возразить. Условие договора, которое сервис не в состоянии
    исполнить, не защищает ни пользователя, ни оператора.

    Блокировка не затрагивает право на средства (п. 9.4): баланс остаётся
    за пользователем, ограничиваются только расходные операции.
    """

    KIND_CHOICES = [
        ('suspended', 'Операции приостановлены'),
        ('blocked', 'Учётная запись заблокирована'),
    ]

    # Основания из п. 9.1 оферты. Перечень закрытый: «иные причины»
    # на практике означают отсутствие основания.
    GROUND_CHOICES = [
        ('terms_violation', 'Нарушение раздела 8 оферты'),
        ('fraud_suspicion', 'Признаки мошенничества'),
        ('authority_request', 'Требование уполномоченного органа'),
        ('verification_failed', 'Недостоверные сведения при верификации'),
        ('age_restriction', 'Пользователь младше 18 лет'),
    ]

    DECISION_CHOICES = [
        ('upheld', 'Ограничение оставлено в силе'),
        ('lifted', 'Ограничение снято'),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='restrictions',
        verbose_name='Пользователь',
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, verbose_name='Вид ограничения')
    ground = models.CharField(max_length=30, choices=GROUND_CHOICES, verbose_name='Основание')
    reason = models.TextField(
        verbose_name='Причина для пользователя',
        help_text='Показывается пользователю и отправляется ему письмом',
    )
    internal_note = models.TextField(
        blank=True, default='', verbose_name='Служебная заметка',
        help_text='Пользователю не показывается',
    )

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions_created', verbose_name='Кто применил',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Когда применено')
    notify_deadline = models.DateTimeField(verbose_name='Срок уведомления')
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name='Уведомление отправлено')

    appeal_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Срок рассмотрения возражений',
    )
    appeal_text = models.TextField(blank=True, default='', verbose_name='Объяснения пользователя')
    appeal_submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Возражение подано')

    decision = models.CharField(
        max_length=20, choices=DECISION_CHOICES, blank=True, default='',
        verbose_name='Решение по возражению',
    )
    decision_comment = models.TextField(blank=True, default='', verbose_name='Мотивировка решения')
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата решения')
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions_decided', verbose_name='Кто рассмотрел',
    )

    lifted_at = models.DateTimeField(null=True, blank=True, verbose_name='Снято')
    lifted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions_lifted', verbose_name='Кто снял',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ограничение по учётной записи'
        verbose_name_plural = 'Ограничения по учётным записям'
        indexes = [models.Index(fields=['user', 'lifted_at'])]

    def __str__(self):
        return f'{self.get_kind_display()} — {self.user} от {self.created_at:%d.%m.%Y}'

    def save(self, *args, **kwargs):
        if not self.notify_deadline:
            # п. 9.2: уведомить в течение 1 рабочего дня
            self.notify_deadline = add_business_days(timezone.now(), 1)
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.lifted_at is None

    @property
    def notification_overdue(self):
        """Срок уведомления по п. 9.2 пропущен."""
        return self.notified_at is None and timezone.now() > self.notify_deadline

    @property
    def appeal_overdue(self):
        """Возражение подано, но решение по нему просрочено (п. 9.3)."""
        return (
            self.appeal_submitted_at is not None
            and not self.decision
            and self.appeal_deadline is not None
            and timezone.now() > self.appeal_deadline
        )

    @classmethod
    def active_for(cls, user):
        """Действующее ограничение пользователя или None."""
        if not getattr(user, 'pk', None):
            return None
        return cls.objects.filter(user=user, lifted_at__isnull=True).order_by('-created_at').first()

    def mark_notified(self):
        self.notified_at = timezone.now()
        self.save(update_fields=['notified_at'])

    def submit_appeal(self, text):
        """
        Приём объяснений пользователя.

        Срок рассмотрения (п. 9.3) отсчитывается от подачи возражения,
        а не от применения ограничения: иначе молчание оператора можно
        было бы «переждать», не начав рассмотрение.
        """
        self.appeal_text = text
        self.appeal_submitted_at = timezone.now()
        self.appeal_deadline = add_business_days(self.appeal_submitted_at, 5)
        self.save(update_fields=['appeal_text', 'appeal_submitted_at', 'appeal_deadline'])

    def resolve(self, moderator, decision, comment):
        """Мотивированное решение по возражению."""
        self.decision = decision
        self.decision_comment = comment
        self.decided_at = timezone.now()
        self.decided_by = moderator
        fields = ['decision', 'decision_comment', 'decided_at', 'decided_by']
        if decision == 'lifted':
            self.lifted_at = self.decided_at
            self.lifted_by = moderator
            fields += ['lifted_at', 'lifted_by']
        self.save(update_fields=fields)

    def lift(self, moderator, comment=''):
        """Снятие ограничения без процедуры обжалования."""
        self.lifted_at = timezone.now()
        self.lifted_by = moderator
        if comment:
            self.decision_comment = comment
        self.save(update_fields=['lifted_at', 'lifted_by', 'decision_comment'])


class DataBreachIncident(models.Model):
    """
    Инцидент с персональными данными и сроки уведомления Роскомнадзора.

    Часть 3.1 ст. 21 152-ФЗ даёт оператору 24 часа на первичное уведомление
    об инциденте и 72 часа на уведомление о результатах внутреннего
    расследования. Сроки считаются с момента, когда об инциденте стало
    известно, — а не с момента, когда его признали инцидентом.

    Политика уже обещала пользователям этот порядок, но нигде его не вела:
    не было ни записи об инциденте, ни отсчёта часов, ни текста уведомления.
    В сутки, когда инцидент случается, писать регламент поздно.

    Модель — рабочий журнал: она не отправляет уведомление сама (РКН
    принимает их через свой портал), но не даёт пропустить срок и
    хранит доказательство того, что срок соблюдён.
    """

    SEVERITY_CHOICES = [
        ('suspected', 'Подозрение на инцидент'),
        ('confirmed', 'Подтверждённый инцидент'),
        ('false_alarm', 'Не подтвердился'),
    ]

    detected_at = models.DateTimeField(
        verbose_name='Когда стало известно',
        help_text='Момент, с которого считаются 24 и 72 часа',
    )
    severity = models.CharField(
        max_length=20, choices=SEVERITY_CHOICES, default='suspected',
        verbose_name='Квалификация',
    )
    summary = models.CharField(max_length=255, verbose_name='Кратко')
    description = models.TextField(verbose_name='Обстоятельства инцидента')
    data_categories = models.TextField(
        verbose_name='Категории данных',
        help_text='Какие именно данные затронуты: паспорт, реквизиты, контакты…',
    )
    affected_count = models.IntegerField(default=0, verbose_name='Число затронутых субъектов')
    suspected_cause = models.TextField(blank=True, default='', verbose_name='Предполагаемая причина')

    initial_notice_deadline = models.DateTimeField(verbose_name='Срок первичного уведомления (24 ч)')
    initial_notice_sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Первичное уведомление отправлено',
    )
    initial_notice_reference = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Входящий номер РКН (первичное)',
    )

    final_notice_deadline = models.DateTimeField(verbose_name='Срок уведомления о расследовании (72 ч)')
    final_notice_sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Уведомление о расследовании отправлено',
    )
    final_notice_reference = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Входящий номер РКН (по расследованию)',
    )

    investigation_result = models.TextField(blank=True, default='', verbose_name='Результаты расследования')
    measures_taken = models.TextField(blank=True, default='', verbose_name='Принятые меры')
    subjects_notified_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Субъекты уведомлены',
    )

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='breach_incidents', verbose_name='Кто зарегистрировал',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Запись создана')

    class Meta:
        ordering = ['-detected_at']
        verbose_name = 'Инцидент с персональными данными'
        verbose_name_plural = 'Инциденты с персональными данными'

    def __str__(self):
        return f'{self.detected_at:%d.%m.%Y %H:%M} — {self.summary}'

    def save(self, *args, **kwargs):
        # Сроки пересчитываются от detected_at всегда: если момент обнаружения
        # уточнили, срок обязан сдвинуться вместе с ним, иначе журнал показывает
        # соблюдение срока, которого не было.
        if self.detected_at:
            self.initial_notice_deadline = self.detected_at + timezone.timedelta(hours=24)
            self.final_notice_deadline = self.detected_at + timezone.timedelta(hours=72)
        super().save(*args, **kwargs)

    @property
    def initial_overdue(self):
        return self.initial_notice_sent_at is None and timezone.now() > self.initial_notice_deadline

    @property
    def final_overdue(self):
        return self.final_notice_sent_at is None and timezone.now() > self.final_notice_deadline

    @property
    def hours_to_initial(self):
        return round((self.initial_notice_deadline - timezone.now()).total_seconds() / 3600, 1)

    @property
    def hours_to_final(self):
        return round((self.final_notice_deadline - timezone.now()).total_seconds() / 3600, 1)

    @property
    def is_open(self):
        """Инцидент считается открытым, пока не закрыты оба уведомления."""
        if self.severity == 'false_alarm':
            return False
        return self.initial_notice_sent_at is None or self.final_notice_sent_at is None


class EmailConfirmation(models.Model):
    """
    Подтверждение адреса электронной почты.

    Адрес не проверялся вовсе: человек вводил что угодно, а потом на этот
    адрес уходили письма о блокировке счёта, о выплатах и ссылка для
    восстановления пароля. Опечатка в адресе означала, что восстановить
    доступ к деньгам нельзя, а чужой адрес — что письма о чужом счёте
    получает посторонний.

    Подтверждение мягкое: пользоваться сервисом можно сразу, но расходные
    операции с деньгами и публикация сбора требуют подтверждённого адреса.
    Смысл в том, чтобы деньги нельзя было завести на адрес, до которого
    невозможно достучаться.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='email_confirmation',
        verbose_name='Пользователь',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='Подтверждён')
    confirmed_email = models.EmailField(
        blank=True, default='', verbose_name='Подтверждённый адрес',
        help_text='Фиксируется отдельно: при смене адреса подтверждение перестаёт действовать',
    )
    last_sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Письмо отправлено')
    # Учётные записи, существовавшие до введения проверки. Считать их
    # подтверждёнными было бы неправдой, а отнимать у них вывод средств
    # без предупреждения — несправедливо: отмечаем отдельно и честно.
    is_legacy = models.BooleanField(
        default=False, verbose_name='Зарегистрирован до введения проверки',
    )

    class Meta:
        verbose_name = 'Подтверждение email'
        verbose_name_plural = 'Подтверждения email'

    def __str__(self):
        state = 'подтверждён' if self.is_confirmed else 'не подтверждён'
        return f'{self.user.username}: {state}'

    @property
    def is_confirmed(self):
        """
        Подтверждён ли текущий адрес пользователя.

        Сравнение с confirmed_email обязательно: иначе смена адреса в профиле
        оставляла бы отметку о подтверждении от прежнего адреса.
        """
        current = (self.user.email or '').strip().lower()
        if not current:
            # Пустой адрес не бывает подтверждённым, даже у старой записи:
            # уведомление о выплате отправить некуда
            return False
        if self.is_legacy:
            return True
        if not self.confirmed_at:
            return False
        return (self.confirmed_email or '').lower() == current

    @property
    def is_actually_confirmed(self):
        """
        Подтверждён ли адрес на самом деле, без скидки на возраст записи.

        Нужно там, где решается, отправлять ли письмо: пользователь
        с отметкой is_legacy вправе подтвердить адрес добровольно, и
        отвечать ему «адрес уже подтверждён» было бы неправдой.
        """
        if not self.confirmed_at:
            return False
        return (self.confirmed_email or '').lower() == (self.user.email or '').strip().lower()

    @property
    def can_resend(self):
        """Не чаще одного письма в несколько минут — иначе форма годится для рассылки."""
        from django.conf import settings

        if not self.last_sent_at:
            return True
        delay = getattr(settings, 'EMAIL_CONFIRMATION_RESEND_SECONDS', 300)
        return timezone.now() - self.last_sent_at > timezone.timedelta(seconds=delay)

    def mark_sent(self):
        self.last_sent_at = timezone.now()
        self.save(update_fields=['last_sent_at'])

    def confirm(self):
        self.confirmed_at = timezone.now()
        self.confirmed_email = self.user.email
        self.is_legacy = False
        self.save(update_fields=['confirmed_at', 'confirmed_email', 'is_legacy'])

    @classmethod
    def for_user(cls, user):
        confirmation, _ = cls.objects.get_or_create(user=user)
        return confirmation

    @classmethod
    def is_email_confirmed(cls, user):
        """Удобная проверка: отсутствие записи означает «не подтверждён»."""
        confirmation = cls.objects.filter(user=user).first()
        return bool(confirmation and confirmation.is_confirmed)
