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
        }


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
        }


# ==================== ЧАТЫ ====================

def message_payload(message, viewer):
    if message is None:
        return None
    deleted = message.sender_id is None
    return {
        'id': message.pk,
        'sender_id': message.sender_id,
        'sender_name': DELETED_USER if deleted else display_name(message.sender),
        'text': 'Сообщение удалено' if deleted and not message.text else message.text,
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
        member = chat.members.filter(user=viewer).first()
        last = chat.messages.select_related('sender').order_by('-id').first()
        peer = None
        if chat.kind == Chat.KIND_DIRECT:
            other = chat.members.exclude(user=viewer).select_related('user').first()
            if other:
                peer = {'id': other.user_id, 'username': other.user.username}
        community = getattr(chat, 'community', None) if chat.kind == Chat.KIND_COMMUNITY else None
        return {
            'id': chat.pk,
            'kind': chat.kind,
            'title': chat_title(chat, viewer),
            'members_count': chat.members.count(),
            'peer': peer,
            'community_id': community.pk if community else None,
            'last_message': message_payload(last, viewer),
            'unread_count': services.unread_count(member) if member else 0,
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
    text = serializers.CharField(max_length=ChatMessage.MAX_LENGTH, trim_whitespace=True)


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
