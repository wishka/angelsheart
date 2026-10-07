from django.utils import timezone
from rest_framework import serializers

from .models import Chat, ChatMessage, Community, CommunityMembership, Interest, SocialProfile
from . import services

DELETED_USER = 'Удалённый пользователь'


class InterestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interest
        fields = ['slug', 'title']


def _profile(user):
    # getattr с запасом: у пользователя может не быть анкеты, и обращение
    # к отсутствующей обратной связи OneToOne бросает исключение
    try:
        return user.social_profile
    except SocialProfile.DoesNotExist:
        return None


def avatar_fields(user):
    """
    has_avatar и avatar_version. Версия — время правки анкеты: приложение
    кладёт фото в кэш по паре (id, версия) и после смены фото не покажет
    старое.
    """
    profile = _profile(user) if user is not None else None
    has = bool(profile and profile.avatar)
    return {
        'has_avatar': has,
        'avatar_version': profile.updated_at.strftime('%Y%m%d%H%M%S') if has else '',
    }


def display_name(user):
    if user is None:
        return DELETED_USER
    profile = _profile(user)
    return (profile.display_name if profile and profile.display_name else '') or user.username


class PersonSerializer(serializers.Serializer):
    """Анкета в поиске. Ни почты, ни баланса — только то, что человек сам открыл."""

    def to_representation(self, user):
        profile = _profile(user)
        return {
            'id': user.pk,
            'username': user.username,
            'display_name': display_name(user),
            'city': profile.city if profile else '',
            'age': profile.age if profile else None,
            'gender': profile.gender if profile else '',
            'about': profile.about if profile else '',
            'interests': InterestSerializer(
                profile.interests.all() if profile else [], many=True,
            ).data,
            'is_blocked': user.pk in _blocked(self.context),
            **avatar_fields(user),
        }


def _blocked(context):
    """Чёрный список зрителя; считается один раз на ответ, а не на каждую строку."""
    if 'blocked_ids' not in context:
        request = context.get('request')
        context['blocked_ids'] = (
            services.blocked_ids(request.user) if request is not None else set()
        )
    return context['blocked_ids']


class MyProfileSerializer(serializers.Serializer):
    """Своя анкета: чтение и правка."""

    display_name = serializers.CharField(max_length=60, required=False, allow_blank=True)
    city = serializers.CharField(max_length=60, required=False, allow_blank=True)
    birth_year = serializers.IntegerField(required=False, allow_null=True)
    gender = serializers.ChoiceField(
        choices=[c for c, _ in SocialProfile.GENDER_CHOICES], required=False, allow_blank=True,
    )
    about = serializers.CharField(max_length=500, required=False, allow_blank=True)
    interests = serializers.ListField(
        child=serializers.SlugField(), required=False, max_length=10,
    )
    is_discoverable = serializers.BooleanField(required=False)

    def validate_birth_year(self, value):
        if value is None:
            return value
        year = timezone.localdate().year
        # Нижняя граница — возраст регистрации по оферте
        if value > year - 14 or value < year - 110:
            raise serializers.ValidationError('Проверьте год рождения')
        return value

    def to_representation(self, profile):
        return {
            'username': profile.user.username,
            'display_name': profile.display_name,
            'city': profile.city,
            'birth_year': profile.birth_year,
            'age': profile.age,
            'gender': profile.gender,
            'about': profile.about,
            'interests': InterestSerializer(profile.interests.all(), many=True).data,
            'is_discoverable': profile.is_discoverable,
            # Отдельно от флажка: если согласие отозвано на сайте, анкета
            # в поиске не видна, хотя флажок включён, — и экран должен
            # сказать почему
            'distribution_consent': services.has_distribution_consent(profile.user),
            'user_id': profile.user_id,
            **avatar_fields(profile.user),
        }


# ==================== ЧАТЫ ====================

HIDDEN_BY_MODERATOR = 'Сообщение скрыто модератором'
HIDDEN_BLOCKED = 'Сообщение от пользователя из вашего чёрного списка'


def message_payload(message, viewer, blocked=()):
    """
    Сообщение для показа.

    Скрытое модератором и пришедшее от того, кого зритель заблокировал,
    отдаются без текста — подменой на сервере, а не флажком для
    приложения: старая версия приложения флажок не знает и показала бы
    текст как есть.
    """
    if message is None:
        return None
    deleted = message.sender_id is None
    text = message.text
    hidden = False
    if deleted and not text and not message.image:
        text = 'Сообщение удалено'
    elif message.is_hidden:
        text, hidden = HIDDEN_BY_MODERATOR, True
    elif message.sender_id in blocked:
        text, hidden = HIDDEN_BLOCKED, True
    return {
        'id': message.pk,
        'sender_id': message.sender_id,
        'sender_name': DELETED_USER if deleted else display_name(message.sender),
        'text': text,
        'is_hidden': hidden,
        # Скрытое сообщение не отдаёт и фото: сама выдача файла тоже это проверяет
        'has_image': bool(message.image) and not hidden,
        'created_at': message.created_at.isoformat(),
        'is_mine': message.sender_id == viewer.pk,
    }


def chat_title(chat, viewer):
    if chat.kind == Chat.KIND_DIRECT:
        peer = chat.members.exclude(user=viewer).select_related('user').first()
        return display_name(peer.user) if peer else DELETED_USER
    return chat.title


class ChatSerializer(serializers.Serializer):
    """Строка списка чатов. Зритель передаётся в context['request']."""

    def to_representation(self, chat):
        viewer = self.context['request'].user
        blocked = _blocked(self.context)
        member = chat.members.filter(user=viewer).first()
        last = chat.messages.select_related('sender').order_by('-id').first()
        peer = None
        block_status = 'none'
        if chat.kind == Chat.KIND_DIRECT:
            other = chat.members.exclude(user=viewer).select_related('user').first()
            if other:
                peer = {
                    'id': other.user_id, 'username': other.user.username,
                    **avatar_fields(other.user),
                }
                if other.user_id in blocked:
                    block_status = 'blocked_by_me'
                elif services.UserBlock.objects.filter(blocker_id=other.user_id, blocked=viewer).exists():
                    block_status = 'blocked_me'
        community = getattr(chat, 'community', None) if chat.kind == Chat.KIND_COMMUNITY else None
        return {
            'id': chat.pk,
            'kind': chat.kind,
            'title': chat_title(chat, viewer),
            'members_count': chat.members.count(),
            'peer': peer,
            'community_id': community.pk if community else None,
            # 'none', 'blocked_by_me', 'blocked_me' — для личного чата:
            # приложение показывает плашку и выключает поле ввода
            'block_status': block_status,
            'last_message': message_payload(last, viewer, blocked),
            'unread_count': services.unread_count(member, blocked) if member else 0,
            'last_activity_at': chat.last_activity_at.isoformat(),
        }


class ChatDetailSerializer(ChatSerializer):
    def to_representation(self, chat):
        data = super().to_representation(chat)
        data['members'] = [
            {'id': m.user_id, 'username': m.user.username, 'display_name': display_name(m.user)}
            for m in chat.members.select_related('user').order_by('joined_at')
        ]
        return data


class ChatCreateSerializer(serializers.Serializer):
    usernames = serializers.ListField(child=serializers.CharField(), min_length=1, max_length=99)
    title = serializers.CharField(max_length=80, required=False, allow_blank=True, default='')


class MessageCreateSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=ChatMessage.MAX_LENGTH, trim_whitespace=True,
                                 required=False, allow_blank=True, default='')
    # FileField, а не ImageField: проверку и пересохранение делает
    # images.prepare, двойная проверка Pillow ничего не добавила бы
    image = serializers.FileField(required=False, allow_null=True, default=None)


# ==================== ГРУППЫ ====================

def my_status(community, viewer):
    membership = services.community_membership(community, viewer)
    if membership is None:
        return 'none'
    if membership.status == CommunityMembership.STATUS_PENDING:
        return 'pending'
    return membership.role  # owner / admin / member


class CommunitySerializer(serializers.Serializer):
    def to_representation(self, community):
        viewer = self.context['request'].user
        status = my_status(community, viewer)
        is_member = status in ('owner', 'admin', 'member')
        return {
            'id': community.pk,
            'name': community.name,
            'description': community.description,
            'topic': InterestSerializer(community.topic).data if community.topic else None,
            'is_private': community.is_private,
            'members_count': community.memberships.filter(
                status=CommunityMembership.STATUS_ACTIVE,
            ).count(),
            'owner_username': community.owner.username,
            'my_status': status,
            # Обсуждение открыто только участникам
            'chat_id': community.chat_id if is_member else None,
            'created_at': community.created_at.isoformat(),
        }


class CommunityDetailSerializer(CommunitySerializer):
    """
    Карточка группы.

    Список участников видят только участники: состав группы по
    интересам — тоже сведения о человеке, и показывать его каждому
    встречному незачем. Заявки видят только администраторы.
    """

    def to_representation(self, community):
        data = super().to_representation(community)
        viewer = self.context['request'].user
        status = data['my_status']

        members = []
        if status in ('owner', 'admin', 'member'):
            members = [
                {
                    'id': m.user_id, 'username': m.user.username,
                    'display_name': display_name(m.user), 'role': m.role,
                }
                for m in community.memberships.filter(
                    status=CommunityMembership.STATUS_ACTIVE,
                ).select_related('user').order_by('role', 'created_at')
            ]
        requests = []
        if status in ('owner', 'admin'):
            requests = [
                {'id': m.user_id, 'username': m.user.username, 'display_name': display_name(m.user)}
                for m in community.memberships.filter(
                    status=CommunityMembership.STATUS_PENDING,
                ).select_related('user').order_by('created_at')
            ]
        data['members'] = members
        data['pending_requests'] = requests
        return data


class CommunityCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
    topic = serializers.SlugField(required=False, allow_blank=True, default='')
    is_private = serializers.BooleanField(required=False, default=False)
