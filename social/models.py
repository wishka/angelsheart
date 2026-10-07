"""
Сообщество: публичный профиль, поиск людей, чаты и группы.

Отдельное приложение, а не продолжение main: у main своя длинная цепочка
миграций и своя предметная область (деньги). Чаты и группы с деньгами не
связаны, и разнесённые миграции не мешают друг другу при слиянии веток.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from main.storage import private_media_storage

User = settings.AUTH_USER_MODEL


class Interest(models.Model):
    """
    Интерес из фиксированного справочника.

    Справочник, а не свободный текст: иначе «кино», «Кино» и «фильмы»
    становятся тремя разными интересами, и поиск по ним ничего не находит.
    Наполняется миграцией 0002.
    """

    slug = models.SlugField(max_length=30, unique=True)
    title = models.CharField('Название', max_length=50)
    position = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'Интерес'
        verbose_name_plural = 'Интересы'
        ordering = ['position', 'title']

    def __str__(self):
        return self.title


class SocialProfile(models.Model):
    """
    Публичная анкета пользователя.

    Видна другим ТОЛЬКО при is_discoverable и действующем согласии на
    распространение персональных данных (ст. 10.1 152-ФЗ): анкета,
    которую находит любой пользователь, — это распространение, а не
    обработка. Согласие проверяется при каждом поиске, а не копируется
    сюда флагом: отзыв согласия на сайте должен убирать человека из
    поиска сразу, без отдельной синхронизации.

    Год рождения вместо даты: для фильтра по возрасту его достаточно,
    а полная дата рождения — лишние персональные данные.
    """

    GENDER_CHOICES = [
        ('', 'Не указан'),
        ('male', 'Мужской'),
        ('female', 'Женский'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='social_profile')
    display_name = models.CharField('Имя для показа', max_length=60, blank=True)
    city = models.CharField('Город', max_length=60, blank=True, db_index=True)
    birth_year = models.PositiveSmallIntegerField('Год рождения', null=True, blank=True)
    gender = models.CharField('Пол', max_length=10, choices=GENDER_CHOICES, blank=True)
    about = models.TextField('О себе', max_length=500, blank=True)
    interests = models.ManyToManyField(Interest, blank=True, related_name='profiles')
    is_discoverable = models.BooleanField('Показывать в поиске', default=False)
    # Фото лежит в приватном хранилище и отдаётся только вью с проверкой
    # прав (people/<id>/avatar/): анкета видна не всем, и фото тоже
    avatar = models.ImageField('Фото', upload_to='avatars/%Y/%m/', blank=True,
                               storage=private_media_storage)
    updated_at = models.DateTimeField(auto_now=True)

    # Служебные поля для поиска, в нижнем регистре. SQLite (база для
    # разработки) сравнивает без учёта регистра только латиницу: «анна»
    # не находило «Анна». Python приводит кириллицу к нижнему регистру
    # правильно, поэтому строка готовится здесь, а ищется точным вхождением.
    search_text = models.CharField(max_length=200, blank=True, editable=False, db_index=True)
    city_key = models.CharField(max_length=60, blank=True, editable=False, db_index=True)

    class Meta:
        verbose_name = 'Анкета'
        verbose_name_plural = 'Анкеты'

    def __str__(self):
        return f'Анкета {self.user}'

    @property
    def age(self):
        if not self.birth_year:
            return None
        return timezone.localdate().year - self.birth_year

    def save(self, *args, **kwargs):
        self.city = self.city.strip()
        self.city_key = self.city.lower()
        self.search_text = ' '.join(
            [self.user.username, self.display_name.strip(), self.city]
        ).lower()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'search_text', 'city_key'}
        super().save(*args, **kwargs)

    @classmethod
    def for_user(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        return profile


class Chat(models.Model):
    """
    Переписка.

    direct — личная, ровно два участника. Пара хранится в direct_key
    («меньший_id:больший_id») с ограничением уникальности: без него два
    одновременных нажатия «Написать» создавали бы два параллельных чата
    между одними и теми же людьми.

    group — групповой чат, созданный пользователем.
    community — обсуждение группы (Community); участники этого чата
    совпадают с участниками группы и меняются вместе с ними.
    """

    KIND_DIRECT = 'direct'
    KIND_GROUP = 'group'
    KIND_COMMUNITY = 'community'
    KIND_CHOICES = [
        (KIND_DIRECT, 'Личный'),
        (KIND_GROUP, 'Групповой'),
        (KIND_COMMUNITY, 'Обсуждение группы'),
    ]

    MAX_MEMBERS = 100

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    title = models.CharField('Название', max_length=80, blank=True)
    direct_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    # Время последнего сообщения: по нему сортируется список чатов
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'Чат'
        verbose_name_plural = 'Чаты'
        ordering = ['-last_activity_at']

    def __str__(self):
        return self.title or f'{self.get_kind_display()} #{self.pk}'

    @staticmethod
    def direct_key_for(user_a, user_b):
        low, high = sorted([user_a.pk, user_b.pk])
        return f'{low}:{high}'


class ChatMember(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_memberships')
    joined_at = models.DateTimeField(auto_now_add=True)
    # До какого сообщения прочитано — по нему считается число непрочитанных
    last_read_message_id = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = 'Участник чата'
        verbose_name_plural = 'Участники чатов'
        constraints = [
            models.UniqueConstraint(fields=['chat', 'user'], name='unique_chat_member'),
        ]


class ChatMessage(models.Model):
    MAX_LENGTH = 2000

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    # Может быть пустым, если в сообщении только фото
    text = models.TextField(max_length=MAX_LENGTH, blank=True)
    # Вложение-изображение; приватное хранилище, выдача — только участникам
    image = models.ImageField('Изображение', upload_to='chat/%Y/%m/', blank=True,
                              storage=private_media_storage)
    created_at = models.DateTimeField(auto_now_add=True)
    # Скрыто модератором или автоматически после нескольких жалоб.
    # Текст не стирается: модератору нужно видеть, на что жаловались,
    # а решение можно отменить.
    is_hidden = models.BooleanField('Скрыто', default=False)

    class Meta:
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'
        ordering = ['id']
        indexes = [models.Index(fields=['chat', 'id'])]

    def __str__(self):
        return f'{self.sender}: {self.text[:40]}'


class Community(models.Model):
    """
    Группа по интересам.

    Открытая — вступить может любой. Закрытая — вступление по заявке,
    которую одобряет владелец или администратор. У каждой группы есть
    своё обсуждение (Chat вида community).
    """

    name = models.CharField('Название', max_length=80)
    description = models.TextField('Описание', max_length=1000, blank=True)
    topic = models.ForeignKey(Interest, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='communities', verbose_name='Тема')
    is_private = models.BooleanField('Закрытая', default=False)
    # CASCADE, а не PROTECT: при удалении учётной записи группа сначала
    # передаётся следующему участнику (services.erase_user_social_data),
    # а сюда доходит только группа, в которой никого больше нет
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_communities')
    chat = models.OneToOneField(Chat, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='community')
    created_at = models.DateTimeField(auto_now_add=True)
    # Нижний регистр для поиска — по той же причине, что в SocialProfile
    search_text = models.TextField(blank=True, editable=False)

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.search_text = f'{self.name} {self.description}'.lower()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'search_text'}
        super().save(*args, **kwargs)


class CommunityMembership(models.Model):
    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_OWNER, 'Владелец'),
        (ROLE_ADMIN, 'Администратор'),
        (ROLE_MEMBER, 'Участник'),
    ]

    STATUS_ACTIVE = 'active'
    STATUS_PENDING = 'pending'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Участник'),
        (STATUS_PENDING, 'Заявка'),
    ]

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='community_memberships')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Участие в группе'
        verbose_name_plural = 'Участие в группах'
        constraints = [
            models.UniqueConstraint(fields=['community', 'user'], name='unique_community_member'),
        ]

    @property
    def can_manage(self):
        return self.status == self.STATUS_ACTIVE and self.role in (self.ROLE_OWNER, self.ROLE_ADMIN)


class UserBlock(models.Model):
    """
    Чёрный список.

    Блокировка действует в обе стороны для личной переписки: ни
    заблокированный не может написать, ни тот, кто заблокировал, — иначе
    можно было бы писать человеку, лишив его возможности ответить.
    Друг друга они не видят в поиске. В общих группах сообщения остаются,
    но у заблокировавшего они скрыты.
    """

    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocks_made')
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocks_received')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Блокировка'
        verbose_name_plural = 'Чёрный список'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_user_block'),
        ]

    def __str__(self):
        return f'{self.blocker} → {self.blocked}'


class MessageReport(models.Model):
    """
    Жалоба на сообщение.

    Текст и автор сообщения копируются в жалобу в момент подачи: автор
    может удалить учётную запись, и тогда текст в чате стирается, а
    модератору всё равно нужно понять, на что жаловались.
    """

    REASON_CHOICES = [
        ('spam', 'Спам или реклама'),
        ('abuse', 'Оскорбления или травля'),
        ('fraud', 'Мошенничество'),
        ('illegal', 'Запрещённый контент'),
        ('other', 'Другое'),
    ]
    STATUS_NEW = 'new'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новая'),
        (STATUS_ACCEPTED, 'Принята — сообщение скрыто'),
        (STATUS_REJECTED, 'Отклонена'),
    ]

    message = models.ForeignKey(ChatMessage, on_delete=models.SET_NULL, null=True,
                                related_name='reports')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_reports')
    reason = models.CharField('Причина', max_length=10, choices=REASON_CHOICES)
    comment = models.TextField('Комментарий', max_length=500, blank=True)
    message_text = models.TextField('Текст сообщения на момент жалобы')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='reports_received', verbose_name='Автор сообщения')
    status = models.CharField('Статус', max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_NEW, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')

    class Meta:
        verbose_name = 'Жалоба на сообщение'
        verbose_name_plural = 'Жалобы на сообщения'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['message', 'reporter'], name='unique_message_report'),
        ]

    def __str__(self):
        return f'Жалоба #{self.pk}: {self.get_reason_display()}'


class DeviceToken(models.Model):
    """
    Адрес телефона для push-уведомлений (токен Firebase Cloud Messaging).

    Токен принадлежит установке приложения, а не человеку: при входе
    под другой учётной записью на том же телефоне он переходит к ней,
    иначе уведомления одного человека приходили бы другому.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=10, default='android')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Устройство для уведомлений'
        verbose_name_plural = 'Устройства для уведомлений'
