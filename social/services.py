"""
Правила сообщества в одном месте.

Вью только разбирают запрос и отдают ответ; всё, что меняет данные,
живёт здесь. Так одно и то же правило («владелец не может выйти из
группы») не расходится между API, сайтом и тестами.
"""

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from main.models import UserConsent

from . import images

from .models import (
    Chat, ChatMember, ChatMessage, Community, CommunityMembership, Interest, MessageReport,
    SocialProfile, UserBlock,
)

DISTRIBUTION = 'distribution'

# После скольких жалоб от разных людей сообщение скрывается до решения
# модератора. Одна жалоба не скрывает ничего: иначе любой мог бы
# «стереть» неудобное сообщение одним нажатием.
REPORTS_TO_HIDE = 3


class SocialError(Exception):
    """Отказ, понятный человеку: текст уходит в ответ API как есть."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ==================== ЧЁРНЫЙ СПИСОК ====================

def blocked_ids(user):
    """Кого заблокировал user."""
    return set(UserBlock.objects.filter(blocker=user).values_list('blocked_id', flat=True))


def blocked_by_ids(user):
    """Кто заблокировал user."""
    return set(UserBlock.objects.filter(blocked=user).values_list('blocker_id', flat=True))


def is_blocked_between(a, b):
    return UserBlock.objects.filter(
        Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a),
    ).exists()


def block_user(user, target):
    if target.pk == user.pk:
        raise SocialError('Нельзя заблокировать самого себя')
    _, created = UserBlock.objects.get_or_create(blocker=user, blocked=target)
    # Блокировка разрывает и подписки в обе стороны: иначе заблокированный
    # продолжал бы получать публикации в ленту
    from .models import Follow
    Follow.objects.filter(
        Q(follower=user, following=target) | Q(follower=target, following=user),
    ).delete()
    if not created:
        return f'{target.username} уже в чёрном списке'
    return f'{target.username} добавлен в чёрный список'


def unblock_user(user, target_id):
    deleted, _ = UserBlock.objects.filter(blocker=user, blocked_id=target_id).delete()
    if not deleted:
        raise SocialError('Этого пользователя нет в чёрном списке', status=404)
    return 'Пользователь удалён из чёрного списка'


# ==================== АНКЕТА И ПОИСК ====================

def has_distribution_consent(user):
    return UserConsent.has_active_consent(user, DISTRIBUTION)


def discoverable_users():
    """
    Кого вообще можно найти.

    Два условия: человек сам включил показ в поиске И у него действует
    согласие на распространение ПДн. Согласие проверяется подзапросом
    при каждом поиске — отзыв согласия убирает анкету из выдачи сразу.
    """
    consent = UserConsent.objects.filter(
        user=OuterRef('pk'),
        consent_type=DISTRIBUTION,
        version=UserConsent.current_version(),
        is_accepted=True,
        revoked_at__isnull=True,
    )
    return User.objects.filter(
        is_active=True,
        social_profile__is_discoverable=True,
    ).filter(Exists(consent))


def search_people(viewer, query='', city='', gender='', age_min=None, age_max=None,
                  interests=None):
    """
    Поиск по анкетам.

    query ищется в имени пользователя, имени для показа и городе.
    interests — список slug: достаточно совпадения хотя бы по одному.
    """
    users = (
        discoverable_users()
        .exclude(pk=viewer.pk)
        # Чёрный список скрывает в обе стороны: заблокированный не должен
        # находить того, кто его заблокировал, и наоборот
        .exclude(pk__in=blocked_ids(viewer) | blocked_by_ids(viewer))
        .select_related('social_profile')
    )

    query = (query or '').strip().lstrip('@').lower()
    if query:
        users = users.filter(social_profile__search_text__contains=query)
    city = (city or '').strip().lower()
    if city:
        users = users.filter(social_profile__city_key=city)
    if gender in ('male', 'female'):
        users = users.filter(social_profile__gender=gender)

    # Возраст пересчитывается в год рождения: «от 18 до 30» в 2026 году —
    # это родившиеся с 1996 по 2008. Возраст по году рождения может быть
    # на год больше настоящего — для поиска собеседников это допустимо.
    this_year = timezone.localdate().year
    if age_min is not None:
        users = users.filter(social_profile__birth_year__lte=this_year - age_min)
    if age_max is not None:
        users = users.filter(social_profile__birth_year__gte=this_year - age_max)

    if interests:
        users = users.filter(social_profile__interests__slug__in=interests).distinct()

    return users.prefetch_related('social_profile__interests').order_by('username')


@transaction.atomic
def update_profile(request, data):
    """
    Сохранение анкеты.

    Включение показа в поиске фиксирует согласие на распространение
    ПДн (с IP и User-Agent, как при регистрации), если его ещё нет:
    флажок в приложении и есть это согласие, и экран говорит об этом
    прямо рядом с переключателем.
    """
    from main.views import record_consents

    profile = SocialProfile.for_user(request.user)

    for field in ('display_name', 'city', 'about', 'gender'):
        if field in data:
            setattr(profile, field, data[field])
    if 'birth_year' in data:
        profile.birth_year = data['birth_year']

    if 'is_discoverable' in data:
        wanted = bool(data['is_discoverable'])
        if wanted and not has_distribution_consent(request.user):
            record_consents(request, request.user, [DISTRIBUTION])
        profile.is_discoverable = wanted

    profile.save()

    if 'interests' in data:
        profile.interests.set(Interest.objects.filter(slug__in=data['interests']))

    return profile


def set_avatar(user, uploaded):
    try:
        prepared = images.prepare(uploaded, images.AVATAR_SIDE, square=True)
    except images.ImageRejected as error:
        raise SocialError(error.message)
    profile = SocialProfile.for_user(user)
    old = profile.avatar.name if profile.avatar else None
    profile.avatar.save(prepared.name, prepared, save=True)
    if old:
        profile.avatar.storage.delete(old)
    return profile


def remove_avatar(user):
    profile = SocialProfile.for_user(user)
    if profile.avatar:
        profile.avatar.delete(save=True)
    return profile


def can_see_avatar(viewer, user):
    """
    Фото видно: самому человеку; всем, если анкета открыта для поиска;
    собеседникам по общему чату. Никогда — тем, кто в чёрном списке
    с любой стороны.
    """
    if viewer.pk == user.pk:
        return True
    if is_blocked_between(viewer, user):
        return False
    if discoverable_users().filter(pk=user.pk).exists():
        return True
    return ChatMember.objects.filter(user=viewer, chat__members__user=user).exists()


# ==================== ЧАТЫ ====================

def membership(chat, user):
    """Участие в чате или отказ 404: чужой чат не должен даже подтверждать своё существование."""
    member = ChatMember.objects.filter(chat=chat, user=user).first()
    if member is None:
        raise SocialError('Чат не найден', status=404)
    return member


def get_or_create_direct_chat(user, other):
    if other.pk == user.pk:
        raise SocialError('Нельзя написать самому себе')
    if not other.is_active:
        raise SocialError('Пользователь не найден', status=404)

    key = Chat.direct_key_for(user, other)
    chat = Chat.objects.filter(direct_key=key).first()
    if chat is not None:
        # Существующую переписку открыть можно и после блокировки —
        # прочитать историю; писать в неё запрещает send_message
        return chat, False
    if is_blocked_between(user, other):
        raise SocialError('Нельзя написать этому пользователю', status=403)

    try:
        with transaction.atomic():
            chat = Chat.objects.create(kind=Chat.KIND_DIRECT, direct_key=key, created_by=user)
            ChatMember.objects.bulk_create([
                ChatMember(chat=chat, user=user),
                ChatMember(chat=chat, user=other),
            ])
            return chat, True
    except IntegrityError:
        # Второй запрос успел создать тот же чат — берём его
        return Chat.objects.get(direct_key=key), False


@transaction.atomic
def create_group_chat(user, title, others):
    title = (title or '').strip()
    if not title:
        raise SocialError('Укажите название чата')
    members = {u.pk: u for u in others if u.pk != user.pk and u.is_active}
    refused = [u.username for u in members.values() if is_blocked_between(user, u)]
    if refused:
        raise SocialError('Нельзя добавить в чат: ' + ', '.join(sorted(refused)), status=403)
    if not members:
        raise SocialError('Добавьте хотя бы одного участника')
    if len(members) + 1 > Chat.MAX_MEMBERS:
        raise SocialError(f'В чате может быть не больше {Chat.MAX_MEMBERS} участников')

    chat = Chat.objects.create(kind=Chat.KIND_GROUP, title=title[:80], created_by=user)
    ChatMember.objects.bulk_create(
        [ChatMember(chat=chat, user=user)]
        + [ChatMember(chat=chat, user=member) for member in members.values()]
    )
    return chat


def send_message(chat, user, text, image=None):
    """
    Отправка сообщения. image — загруженный файл: он проверяется и
    пересохраняется без метаданных (images.prepare). Текст или фото —
    хотя бы одно из двух.
    """
    member = membership(chat, user)
    if chat.kind == Chat.KIND_DIRECT:
        peer = chat.members.exclude(user=user).first()
        if peer is not None and is_blocked_between(user, peer.user):
            raise SocialError(
                'Переписка недоступна: один из вас в чёрном списке у другого', status=403,
            )
    text = (text or '').strip()
    if not text and image is None:
        raise SocialError('Сообщение пустое')
    if len(text) > ChatMessage.MAX_LENGTH:
        raise SocialError(f'Сообщение длиннее {ChatMessage.MAX_LENGTH} символов')
    prepared = None
    if image is not None:
        try:
            prepared = images.prepare(image, images.CHAT_SIDE)
        except images.ImageRejected as error:
            raise SocialError(error.message)

    with transaction.atomic():
        message = ChatMessage(chat=chat, sender=user, text=text)
        if prepared is not None:
            message.image.save(prepared.name, prepared, save=False)
        message.save()
        Chat.objects.filter(pk=chat.pk).update(last_activity_at=message.created_at)
        # Своё сообщение прочитано по определению
        member.last_read_message_id = message.pk
        member.save(update_fields=['last_read_message_id'])

    from . import push
    push.notify_new_message(message)
    return message


def mark_read(member, last_message_id):
    if last_message_id and last_message_id > member.last_read_message_id:
        member.last_read_message_id = last_message_id
        member.save(update_fields=['last_read_message_id'])


def unread_count(member, hidden_senders=()):
    return ChatMessage.objects.filter(
        chat_id=member.chat_id, id__gt=member.last_read_message_id, is_hidden=False,
    ).exclude(sender_id=member.user_id).exclude(sender_id__in=hidden_senders).count()


@transaction.atomic
def leave_chat(chat, user):
    member = membership(chat, user)
    if chat.kind == Chat.KIND_DIRECT:
        raise SocialError('Из личного чата выйти нельзя')
    if chat.kind == Chat.KIND_COMMUNITY:
        raise SocialError('Это обсуждение группы — чтобы уйти, выйдите из группы')
    member.delete()
    if not chat.members.exists():
        chat.delete()


def edit_message(chat, user, message_id, text):
    membership(chat, user)
    message = ChatMessage.objects.filter(chat=chat, pk=message_id, sender=user,
                                         deleted_at__isnull=True).first()
    if message is None:
        raise SocialError('Изменить можно только своё сообщение', status=404)
    if message.is_hidden:
        raise SocialError('Сообщение скрыто модератором — изменить его нельзя')
    text = (text or '').strip()
    if not text and not message.image:
        raise SocialError('Сообщение пустое')
    if len(text) > ChatMessage.MAX_LENGTH:
        raise SocialError(f'Сообщение длиннее {ChatMessage.MAX_LENGTH} символов')
    message.text = text
    message.edited_at = timezone.now()
    message.save()
    return message


def delete_message(chat, user, message_id):
    """
    Удаление своего сообщения у всех: текст и фото стираются, в чате
    остаётся «Сообщение удалено» — чтобы ответы на него не повисали
    без контекста. Жалобы, поданные раньше, хранят свою копию текста.
    """
    membership(chat, user)
    message = ChatMessage.objects.filter(chat=chat, pk=message_id, sender=user,
                                         deleted_at__isnull=True).first()
    if message is None:
        raise SocialError('Удалить можно только своё сообщение', status=404)
    if message.image:
        message.image.delete(save=False)
    message.text = ''
    message.deleted_at = timezone.now()
    message.save()
    return message


# ==================== ГРУППЫ ====================

def community_membership(community, user):
    return CommunityMembership.objects.filter(community=community, user=user).first()


def _add_to_chat(community, user):
    if community.chat_id:
        ChatMember.objects.get_or_create(chat_id=community.chat_id, user=user)


def _remove_from_chat(community, user):
    if community.chat_id:
        ChatMember.objects.filter(chat_id=community.chat_id, user=user).delete()


@transaction.atomic
def create_community(user, name, description='', topic=None, is_private=False):
    name = (name or '').strip()
    if not name:
        raise SocialError('Укажите название группы')
    chat = Chat.objects.create(kind=Chat.KIND_COMMUNITY, title=name[:80], created_by=user)
    community = Community.objects.create(
        name=name[:80], description=(description or '').strip(), topic=topic,
        is_private=is_private, owner=user, chat=chat,
    )
    CommunityMembership.objects.create(
        community=community, user=user,
        role=CommunityMembership.ROLE_OWNER, status=CommunityMembership.STATUS_ACTIVE,
    )
    ChatMember.objects.create(chat=chat, user=user)
    return community


@transaction.atomic
def join_community(community, user):
    """Возвращает текст для человека: вступил или отправил заявку."""
    existing = community_membership(community, user)
    if existing is not None:
        if existing.status == CommunityMembership.STATUS_PENDING:
            raise SocialError('Заявка уже отправлена')
        raise SocialError('Вы уже в группе')

    if community.is_private:
        CommunityMembership.objects.create(
            community=community, user=user, status=CommunityMembership.STATUS_PENDING,
        )
        return 'Заявка отправлена. Её рассмотрит администратор группы.'

    CommunityMembership.objects.create(community=community, user=user)
    _add_to_chat(community, user)
    return f'Вы вступили в группу «{community.name}»'


@transaction.atomic
def leave_community(community, user):
    existing = community_membership(community, user)
    if existing is None:
        raise SocialError('Вы не состоите в группе')
    if existing.role == CommunityMembership.ROLE_OWNER:
        raise SocialError('Владелец не может выйти из своей группы')
    was_pending = existing.status == CommunityMembership.STATUS_PENDING
    existing.delete()
    _remove_from_chat(community, user)
    return 'Заявка отозвана' if was_pending else 'Вы вышли из группы'


def _require_manager(community, user):
    manager = community_membership(community, user)
    if manager is None or not manager.can_manage:
        raise SocialError('Это может только администратор группы', status=403)


@transaction.atomic
def approve_request(community, manager, user_id):
    _require_manager(community, manager)
    request = CommunityMembership.objects.filter(
        community=community, user_id=user_id, status=CommunityMembership.STATUS_PENDING,
    ).select_related('user').first()
    if request is None:
        raise SocialError('Заявка не найдена', status=404)
    request.status = CommunityMembership.STATUS_ACTIVE
    request.save(update_fields=['status'])
    _add_to_chat(community, request.user)
    return f'{request.user.username} принят в группу'


@transaction.atomic
def decline_request(community, manager, user_id):
    _require_manager(community, manager)
    deleted, _ = CommunityMembership.objects.filter(
        community=community, user_id=user_id, status=CommunityMembership.STATUS_PENDING,
    ).delete()
    if not deleted:
        raise SocialError('Заявка не найдена', status=404)
    return 'Заявка отклонена'


def _owner(community, user):
    owner = community_membership(community, user)
    if owner is None or owner.role != CommunityMembership.ROLE_OWNER:
        raise SocialError('Это может только владелец группы', status=403)
    return owner


def _active_member(community, user_id):
    member = CommunityMembership.objects.filter(
        community=community, user_id=user_id, status=CommunityMembership.STATUS_ACTIVE,
    ).select_related('user').first()
    if member is None:
        raise SocialError('Участник не найден', status=404)
    return member


@transaction.atomic
def set_member_role(community, actor, user_id, role):
    _owner(community, actor)
    if role not in (CommunityMembership.ROLE_ADMIN, CommunityMembership.ROLE_MEMBER):
        raise SocialError('Роль — admin или member')
    member = _active_member(community, user_id)
    if member.role == CommunityMembership.ROLE_OWNER:
        raise SocialError('Роль владельца меняется передачей группы')
    member.role = role
    member.save(update_fields=['role'])
    title = 'администратор' if role == CommunityMembership.ROLE_ADMIN else 'участник'
    return f'{member.user.username} теперь {title}'


@transaction.atomic
def transfer_ownership(community, actor, user_id):
    """Группа переходит другому участнику; прежний владелец остаётся администратором."""
    current = _owner(community, actor)
    heir = _active_member(community, user_id)
    if heir.pk == current.pk:
        raise SocialError('Вы и так владелец')
    current.role = CommunityMembership.ROLE_ADMIN
    current.save(update_fields=['role'])
    heir.role = CommunityMembership.ROLE_OWNER
    heir.save(update_fields=['role'])
    community.owner = heir.user
    community.save(update_fields=['owner'])
    return f'Владелец группы теперь {heir.user.username}'


@transaction.atomic
def remove_member(community, actor, user_id):
    """Исключить участника. Администратор исключает только рядовых участников."""
    manager = community_membership(community, actor)
    if manager is None or not manager.can_manage:
        raise SocialError('Это может только администратор группы', status=403)
    member = _active_member(community, user_id)
    if member.role == CommunityMembership.ROLE_OWNER:
        raise SocialError('Владельца исключить нельзя')
    if member.role == CommunityMembership.ROLE_ADMIN and manager.role != CommunityMembership.ROLE_OWNER:
        raise SocialError('Администратора может исключить только владелец', status=403)
    member.delete()
    _remove_from_chat(community, member.user)
    return f'{member.user.username} исключён из группы'


def update_community(community, actor, name=None, description=None, topic=None, is_private=None):
    manager = community_membership(community, actor)
    if manager is None or not manager.can_manage:
        raise SocialError('Это может только администратор группы', status=403)
    if name is not None:
        name = name.strip()
        if not name:
            raise SocialError('Укажите название группы')
        community.name = name[:80]
        if community.chat_id:
            Chat.objects.filter(pk=community.chat_id).update(title=community.name)
    if description is not None:
        community.description = description.strip()[:1000]
    if topic is not None:
        community.topic = Interest.objects.filter(slug=topic).first() if topic else None
    if is_private is not None:
        community.is_private = bool(is_private)
    community.save()
    return 'Группа обновлена'


@transaction.atomic
def delete_community(community, actor):
    _owner(community, actor)
    if community.chat_id:
        for message in ChatMessage.objects.filter(chat_id=community.chat_id).exclude(image=''):
            message.image.delete(save=False)
        Chat.objects.filter(pk=community.chat_id).delete()
    community.delete()
    return 'Группа удалена'


# ==================== ЖАЛОБЫ ====================

def report_message(user, chat, message_id, reason, comment='', also_block=False):
    """
    Жалоба на сообщение. Возвращает текст для человека.

    Жаловаться можно только на сообщение из своего чата и только один
    раз. После REPORTS_TO_HIDE жалоб от разных людей сообщение скрывается
    до решения модератора.
    """
    membership(chat, user)
    message = ChatMessage.objects.filter(chat=chat, pk=message_id).select_related('sender').first()
    if message is None:
        raise SocialError('Сообщение не найдено', status=404)
    if message.sender_id == user.pk:
        raise SocialError('Нельзя пожаловаться на своё сообщение')
    if message.sender_id is None or not (message.text or message.image):
        raise SocialError('Сообщение уже удалено')
    if reason not in dict(MessageReport.REASON_CHOICES):
        raise SocialError('Укажите причину жалобы')

    with transaction.atomic():
        _, created = MessageReport.objects.get_or_create(
            message=message, reporter=user,
            defaults={
                'reason': reason,
                'comment': (comment or '').strip()[:500],
                'message_text': message.text + (' [фото]' if message.image else ''),
                'sender': message.sender,
            },
        )
        if not created:
            raise SocialError('Вы уже пожаловались на это сообщение')

        reporters = MessageReport.objects.filter(
            message=message, status=MessageReport.STATUS_NEW,
        ).values('reporter').distinct().count()
        if reporters >= REPORTS_TO_HIDE and not message.is_hidden:
            message.is_hidden = True
            message.save(update_fields=['is_hidden'])

        text = 'Жалоба отправлена. Модератор рассмотрит её.'
        if also_block:
            block_user(user, message.sender)
            text += f' {message.sender.username} добавлен в чёрный список.'
    return text


def resolve_reports(message, moderator, accept):
    """
    Решение модератора по всем новым жалобам на сообщение.

    accept=True — сообщение скрывается; False — жалобы отклоняются и
    сообщение возвращается, если его скрыло автоматическое правило.
    """
    with transaction.atomic():
        MessageReport.objects.filter(message=message, status=MessageReport.STATUS_NEW).update(
            status=MessageReport.STATUS_ACCEPTED if accept else MessageReport.STATUS_REJECTED,
            resolved_at=timezone.now(),
            resolved_by=moderator,
        )
        message.is_hidden = accept
        message.save(update_fields=['is_hidden'])


# ==================== УДАЛЕНИЕ УЧЁТНОЙ ЗАПИСИ ====================

def erase_user_social_data(user):
    """
    Уничтожение данных сообщества при удалении учётной записи.

    Анкета удаляется целиком. Сообщения остаются в чатах собеседников —
    иначе у них рвётся переписка, — но без текста и без автора: на
    экране это «Сообщение удалено». Группы, которыми человек владел,
    переходят к следующему по старшинству администратору или участнику;
    группа, в которой никого не осталось, удаляется вместе с обсуждением.
    """
    # Файлы удаляются явно: удаление записи не стирает файл с диска
    for profile in SocialProfile.objects.filter(user=user).exclude(avatar=''):
        profile.avatar.delete(save=False)
    SocialProfile.objects.filter(user=user).delete()
    for message in ChatMessage.objects.filter(sender=user).exclude(image=''):
        message.image.delete(save=False)
    ChatMessage.objects.filter(sender=user).update(text='', image='', sender=None)

    for community in Community.objects.filter(owner=user):
        heir = (
            CommunityMembership.objects
            .filter(community=community, status=CommunityMembership.STATUS_ACTIVE)
            .exclude(user=user)
            .order_by('role', 'created_at')  # 'admin' < 'member' по алфавиту
            .first()
        )
        if heir is None:
            if community.chat_id:
                Chat.objects.filter(pk=community.chat_id).delete()
            community.delete()
            continue
        heir.role = CommunityMembership.ROLE_OWNER
        heir.save(update_fields=['role'])
        community.owner = heir.user
        community.save(update_fields=['owner'])

    CommunityMembership.objects.filter(user=user).delete()
    ChatMember.objects.filter(user=user).delete()
    UserBlock.objects.filter(Q(blocker=user) | Q(blocked=user)).delete()
    from .feed import erase_user_feed
    erase_user_feed(user)
    from .models import DeviceToken
    DeviceToken.objects.filter(user=user).delete()
    # Жалобы — и поданные человеком, и на его сообщения — остаются:
    # это сведения о возможном нарушении, и модератор должен довести
    # разбор до конца. Учётная запись при удалении обезличивается, так
    # что в жалобе остаётся только логин вида deleted_…
