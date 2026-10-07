"""
API сообщества: /api/profile/, /api/people/, /api/chats/, /api/groups/.

Отдельная ставка ограничения запросов ('social'): открытый чат опрашивает
сервер каждые несколько секунд, и общий лимит 1000 запросов в сутки
кончался бы за час переписки — после чего вставали бы и переводы.
"""

from django.contrib.auth.models import User
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import Chat, ChatMessage, Community, CommunityMembership, Interest, UserBlock
from .serializers import (
    ChatCreateSerializer, ChatDetailSerializer, ChatSerializer, CommunityCreateSerializer,
    CommunityDetailSerializer, CommunitySerializer, InterestSerializer, MessageCreateSerializer,
    MyProfileSerializer, PersonSerializer, message_payload,
)


class SocialThrottle(ScopedRateThrottle):
    scope = 'social'


class SocialMixin:
    permission_classes = [IsAuthenticated]
    throttle_classes = [SocialThrottle]

    @staticmethod
    def refuse(error):
        return Response({'error': error.message}, status=error.status)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ==================== АНКЕТА ====================

class InterestListView(SocialMixin, APIView):
    def get(self, request):
        return Response(InterestSerializer(Interest.objects.all(), many=True).data)


class MyProfileView(SocialMixin, APIView):
    def get(self, request):
        profile = services.SocialProfile.for_user(request.user)
        return Response(MyProfileSerializer(profile).data)

    def patch(self, request):
        serializer = MyProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = services.update_profile(request, serializer.validated_data)
        return Response(MyProfileSerializer(profile).data)

    def put(self, request):
        # Приложение шлёт PUT: HttpURLConnection на Android не умеет PATCH
        # и бросает ProtocolException. Поля, которых нет в запросе, не
        # трогаются — по смыслу это тот же частичный PATCH.
        return self.patch(request)


class MyAvatarView(SocialMixin, APIView):
    """POST multipart с полем image — новое фото; DELETE — убрать фото."""

    def post(self, request):
        try:
            profile = services.set_avatar(request.user, request.FILES.get('image'))
        except services.SocialError as error:
            return self.refuse(error)
        return Response(MyProfileSerializer(profile).data)

    def delete(self, request):
        profile = services.remove_avatar(request.user)
        return Response(MyProfileSerializer(profile).data)


def _file_response(field):
    # private, max-age: приложение кэширует само, а промежуточным
    # прокси хранить чужие фото незачем
    response = FileResponse(field.open('rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, max-age=86400'
    return response


# ==================== ЛЮДИ ====================

class PeopleViewSet(SocialMixin, viewsets.GenericViewSet):
    """
    Поиск людей.

    Параметры: q, city, gender (male/female), age_min, age_max,
    interests (slug через запятую). В выдаче только те, кто сам включил
    показ в поиске и дал согласие на распространение ПДн.
    """

    serializer_class = PersonSerializer

    def get_queryset(self):
        params = self.request.query_params
        interests = [s for s in (params.get('interests') or '').split(',') if s.strip()]
        return services.search_people(
            self.request.user,
            query=params.get('q', ''),
            city=params.get('city', ''),
            gender=params.get('gender', ''),
            age_min=_int(params.get('age_min')),
            age_max=_int(params.get('age_max')),
            interests=[s.strip() for s in interests],
        )

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(
            PersonSerializer(page, many=True, context={'request': request}).data,
        )

    def retrieve(self, request, pk=None):
        user = (
            services.discoverable_users()
            # Тот, кто заблокировал зрителя, для него не существует
            .exclude(pk__in=services.blocked_by_ids(request.user))
            .filter(pk=_int(pk)).first()
        )
        if user is None and _int(pk) == request.user.pk:
            user = request.user
        if user is None:
            return Response({'error': 'Пользователь не найден или скрыл анкету'},
                            status=status.HTTP_404_NOT_FOUND)
        data = PersonSerializer(user, context={'request': request}).data
        from . import feed
        data.update(feed.follow_counts(user))
        data['is_following'] = feed.Follow.objects.filter(follower=request.user, following=user).exists()
        data['can_follow'] = feed.can_follow(request.user, user)
        return Response(data)

    @action(detail=True, methods=['post'])
    def follow(self, request, pk=None):
        """{follow: true|false} — подписаться или отписаться."""
        from . import feed
        user = User.objects.filter(pk=_int(pk), is_active=True).first()
        if user is None:
            return Response({'error': 'Пользователь не найден'}, status=status.HTTP_404_NOT_FOUND)
        wanted = str(request.data.get('follow', 'true')).lower() in ('1', 'true')
        try:
            text = feed.set_follow(request.user, user, wanted)
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text, 'is_following': wanted, **feed.follow_counts(user)})

    @action(detail=True, methods=['get'])
    def posts(self, request, pk=None):
        from . import feed
        from .serializers import post_payload
        user = User.objects.filter(pk=_int(pk), is_active=True).first()
        if user is None:
            return Response({'error': 'Пользователь не найден'}, status=status.HTTP_404_NOT_FOUND)
        items, next_before = feed.posts_of(request.user, user, _int(request.query_params.get('before')))
        return Response({'results': [post_payload(p, request.user) for p in items],
                         'next_before': next_before})

    @action(detail=True, methods=['get'])
    def avatar(self, request, pk=None):
        user = User.objects.filter(pk=_int(pk), is_active=True).select_related('social_profile').first()
        profile = getattr(user, 'social_profile', None) if user else None
        if profile is None or not profile.avatar or not services.can_see_avatar(request.user, user):
            return Response({'error': 'Фото нет'}, status=status.HTTP_404_NOT_FOUND)
        return _file_response(profile.avatar)


# ==================== ЧАТЫ ====================

class ChatViewSet(SocialMixin, viewsets.GenericViewSet):
    serializer_class = ChatSerializer

    def get_queryset(self):
        return Chat.objects.filter(members__user=self.request.user).order_by('-last_activity_at')

    def _chat(self, pk):
        chat = get_object_or_404(Chat, pk=_int(pk))
        services.membership(chat, self.request.user)
        return chat

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        data = ChatSerializer(page, many=True, context={'request': request}).data
        return self.get_paginated_response(data)

    def retrieve(self, request, pk=None):
        try:
            chat = self._chat(pk)
        except services.SocialError as error:
            return self.refuse(error)
        return Response(ChatDetailSerializer(chat, context={'request': request}).data)

    def create(self, request):
        """
        Новый чат.

        Один получатель без названия — личный чат (если он уже есть,
        возвращается он же). Несколько получателей — групповой, название
        обязательно. Получатель указывается точным именем пользователя,
        как при переводе: написать можно и тому, кого нет в поиске.
        """
        serializer = ChatCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        names = {n.strip().lstrip('@') for n in serializer.validated_data['usernames'] if n.strip()}
        users = list(User.objects.filter(username__in=names, is_active=True))
        missing = names - {u.username for u in users}
        if missing:
            return Response({'error': 'Не найдены: ' + ', '.join(sorted(missing))},
                            status=status.HTTP_400_BAD_REQUEST)

        title = serializer.validated_data['title'].strip()
        try:
            if len(users) == 1 and not title:
                chat, created = services.get_or_create_direct_chat(request.user, users[0])
            else:
                chat, created = services.create_group_chat(request.user, title, users), True
        except services.SocialError as error:
            return self.refuse(error)

        return Response(
            ChatDetailSerializer(chat, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get', 'post'])
    def messages(self, request, pk=None):
        """
        GET — сообщения: последние 50; ?after=<id> — новые после id
        (так приложение опрашивает открытый чат); ?before=<id> — более
        ранние. Чтение отмечает сообщения прочитанными.
        POST {text} — отправить сообщение.
        """
        try:
            chat = self._chat(pk)
        except services.SocialError as error:
            return self.refuse(error)

        if request.method == 'POST':
            serializer = MessageCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                message = services.send_message(
                    chat, request.user,
                    serializer.validated_data['text'],
                    serializer.validated_data['image'],
                )
            except services.SocialError as error:
                return self.refuse(error)
            return Response(message_payload(message, request.user), status=status.HTTP_201_CREATED)

        blocked = services.blocked_ids(request.user)

        limit = 50
        messages = ChatMessage.objects.filter(chat=chat).select_related('sender')
        after, before = _int(request.query_params.get('after')), _int(request.query_params.get('before'))
        if after is not None:
            batch = list(messages.filter(id__gt=after).order_by('id')[:limit + 1])
            has_more = len(batch) > limit
            batch = batch[:limit]
        else:
            if before is not None:
                messages = messages.filter(id__lt=before)
            batch = list(messages.order_by('-id')[:limit + 1])
            has_more = len(batch) > limit
            batch = list(reversed(batch[:limit]))

        member = services.membership(chat, request.user)
        if batch:
            services.mark_read(member, batch[-1].pk)

        # Правки и удаления уже показанных сообщений: приложение передаёт
        # время прошлого опроса и заменяет у себя изменённые сообщения
        changed = []
        since = parse_datetime(request.query_params.get('changed_since') or '')
        if since is not None:
            fresh = {m.pk for m in batch}
            changed = [
                message_payload(m, request.user, blocked)
                for m in messages.filter(modified_at__gt=since).order_by('-id')[:100]
                if m.pk not in fresh
            ]

        return Response({
            'results': [message_payload(m, request.user, blocked) for m in batch],
            'changed': changed,
            'has_more': has_more,
            'server_time': timezone.now().isoformat(),
        })

    @action(detail=True, methods=['put', 'delete'], url_path=r'messages/(?P<message_id>[0-9]+)')
    def message(self, request, pk=None, message_id=None):
        """PUT {text} — исправить своё сообщение; DELETE — удалить у всех."""
        try:
            chat = self._chat(pk)
            if request.method == 'DELETE':
                message = services.delete_message(chat, request.user, _int(message_id))
            else:
                message = services.edit_message(chat, request.user, _int(message_id),
                                                request.data.get('text', ''))
        except services.SocialError as error:
            return self.refuse(error)
        return Response(message_payload(message, request.user))

    @action(detail=True, methods=['get'], url_path=r'messages/(?P<message_id>[0-9]+)/image')
    def image(self, request, pk=None, message_id=None):
        """Фото из сообщения — только участникам чата и только не скрытое."""
        try:
            chat = self._chat(pk)
        except services.SocialError as error:
            return self.refuse(error)
        message = ChatMessage.objects.filter(chat=chat, pk=_int(message_id)).first()
        if (message is None or not message.image or message.is_hidden
                or message.sender_id in services.blocked_ids(request.user)):
            return Response({'error': 'Изображения нет'}, status=status.HTTP_404_NOT_FOUND)
        return _file_response(message.image)

    @action(detail=True, methods=['post'], url_path=r'messages/(?P<message_id>[0-9]+)/report')
    def report(self, request, pk=None, message_id=None):
        """
        Жалоба на сообщение: {reason, comment, block}. reason — spam,
        abuse, fraud, illegal, other. block=true сразу добавляет автора
        в чёрный список.
        """
        try:
            chat = self._chat(pk)
            text = services.report_message(
                request.user, chat, _int(message_id),
                reason=request.data.get('reason', ''),
                comment=request.data.get('comment', ''),
                also_block=str(request.data.get('block', '')).lower() in ('1', 'true'),
            )
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        try:
            chat = self._chat(pk)
            services.leave_chat(chat, request.user)
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': 'Вы вышли из чата'})


# ==================== ГРУППЫ ====================

class CommunityViewSet(SocialMixin, viewsets.GenericViewSet):
    """
    Группы. Параметры списка: search, topic (slug), mine=1 — только свои
    (включая поданные заявки).
    """

    serializer_class = CommunitySerializer

    def get_queryset(self):
        params = self.request.query_params
        groups = Community.objects.select_related('topic', 'owner').order_by('-created_at')
        search = (params.get('search') or '').strip().lower()
        if search:
            groups = groups.filter(search_text__contains=search)
        topic = (params.get('topic') or '').strip()
        if topic:
            groups = groups.filter(topic__slug=topic)
        if params.get('mine') in ('1', 'true'):
            groups = groups.filter(memberships__user=self.request.user).distinct()
        return groups

    def _context(self):
        return {'request': self.request}

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(
            CommunitySerializer(page, many=True, context=self._context()).data,
        )

    def retrieve(self, request, pk=None):
        community = get_object_or_404(Community, pk=_int(pk))
        return Response(CommunityDetailSerializer(community, context=self._context()).data)

    def create(self, request):
        serializer = CommunityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        topic = Interest.objects.filter(slug=data['topic']).first() if data['topic'] else None
        try:
            community = services.create_community(
                request.user, data['name'], data['description'], topic, data['is_private'],
            )
        except services.SocialError as error:
            return self.refuse(error)
        return Response(CommunityDetailSerializer(community, context=self._context()).data,
                        status=status.HTTP_201_CREATED)

    def _do(self, pk, operation, *args):
        community = get_object_or_404(Community, pk=_int(pk))
        try:
            text = operation(community, self.request.user, *args)
        except services.SocialError as error:
            return self.refuse(error)
        community.refresh_from_db()
        data = CommunityDetailSerializer(community, context=self._context()).data
        data['message'] = text
        return Response(data)

    @action(detail=True, methods=['post'])
    def join(self, request, pk=None):
        return self._do(pk, services.join_community)

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        return self._do(pk, services.leave_community)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._do(pk, services.approve_request, _int(request.data.get('user_id')))

    @action(detail=True, methods=['post'])
    def decline(self, request, pk=None):
        return self._do(pk, services.decline_request, _int(request.data.get('user_id')))

    @action(detail=True, methods=['post'])
    def role(self, request, pk=None):
        """{user_id, role: admin|member} — только владелец."""
        return self._do(pk, services.set_member_role, _int(request.data.get('user_id')),
                        request.data.get('role', ''))

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """{user_id} — передать группу участнику; только владелец."""
        return self._do(pk, services.transfer_ownership, _int(request.data.get('user_id')))

    @action(detail=True, methods=['post'])
    def remove(self, request, pk=None):
        """{user_id} — исключить участника; владелец или администратор."""
        return self._do(pk, services.remove_member, _int(request.data.get('user_id')))

    def update(self, request, pk=None):
        """PUT {name, description, topic, is_private} — правка группы администратором."""
        data = request.data
        is_private = data.get('is_private')
        return self._do(
            pk, services.update_community,
            data.get('name'), data.get('description'), data.get('topic'),
            None if is_private is None else str(is_private).lower() in ('1', 'true'),
        )

    def destroy(self, request, pk=None):
        community = get_object_or_404(Community, pk=_int(pk))
        try:
            text = services.delete_community(community, request.user)
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text})


# ==================== ЧЁРНЫЙ СПИСОК ====================

class BlockViewSet(SocialMixin, viewsets.GenericViewSet):
    """
    GET — свой чёрный список; POST {username} или {user_id} — добавить;
    DELETE /api/blocks/<user_id>/ — убрать.
    """

    serializer_class = PersonSerializer

    def list(self, request):
        blocks = UserBlock.objects.filter(blocker=request.user).select_related('blocked')
        from .serializers import display_name
        return Response([
            {
                'id': b.blocked_id,
                'username': b.blocked.username,
                'display_name': display_name(b.blocked),
                'created_at': b.created_at.isoformat(),
            }
            for b in blocks
        ])

    def create(self, request):
        username = (request.data.get('username') or '').strip().lstrip('@')
        user_id = _int(request.data.get('user_id'))
        users = User.objects.filter(is_active=True)
        target = (users.filter(pk=user_id) if user_id else users.filter(username=username)).first()
        if target is None:
            return Response({'error': 'Пользователь не найден'}, status=status.HTTP_404_NOT_FOUND)
        try:
            text = services.block_user(request.user, target)
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text, 'id': target.pk}, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        try:
            text = services.unblock_user(request.user, _int(pk))
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text})


# ==================== УВЕДОМЛЕНИЯ ====================

class DeviceView(SocialMixin, APIView):
    """
    POST /api/devices/ {token} — телефон получает уведомления.
    POST /api/devices/unregister/ {token} — перестаёт (при выходе).
    Отдельный адрес вместо DELETE с телом: HttpURLConnection на части
    версий Android не умеет отправлять тело с DELETE.
    """

    def post(self, request, unregister=False):
        from . import push

        token = request.data.get('token', '')
        if unregister:
            push.unregister(request.user, token)
            return Response({'message': 'Уведомления отключены'})
        if not push.register(request.user, token):
            return Response({'error': 'Нет токена устройства'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'message': 'Уведомления включены', 'push_enabled': push.is_enabled()})


# ==================== ЛЕНТА ====================

class FeedView(SocialMixin, APIView):
    """
    GET /api/feed/?scope=following|all&before=<id>
    following — свои публикации и тех, на кого подписан; all — публичные
    публикации всех, кого можно видеть. Страницы по курсору next_before.
    """

    def get(self, request):
        from . import feed
        from .serializers import post_payload
        scope = 'all' if request.query_params.get('scope') == 'all' else 'following'
        items, next_before = feed.feed(request.user, scope, _int(request.query_params.get('before')))
        return Response({'results': [post_payload(p, request.user) for p in items],
                         'next_before': next_before})


class PostViewSet(SocialMixin, viewsets.GenericViewSet):
    """
    POST /api/posts/ (multipart: text, image, visibility) — опубликовать;
    GET/PUT/DELETE /api/posts/<id>/ — посмотреть, изменить, удалить свою;
    POST /api/posts/<id>/like/ {liked}; /comments/ GET и POST;
    DELETE /api/posts/<id>/comments/<cid>/; POST /api/posts/<id>/report/.
    """

    def _payload(self, post):
        from .serializers import post_payload
        return post_payload(post, self.request.user)

    def create(self, request):
        from . import feed
        try:
            post, notice = feed.create_post(
                request.user, request.data.get('text', ''), request.FILES.get('image'),
                request.data.get('visibility', 'followers'),
            )
            data = self._payload(feed.get_post(request.user, post.pk))
        except services.SocialError as error:
            return self.refuse(error)
        data['notice'] = notice
        return Response(data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        from . import feed
        try:
            return Response(self._payload(feed.get_post(request.user, _int(pk))))
        except services.SocialError as error:
            return self.refuse(error)

    def update(self, request, pk=None):
        from . import feed
        try:
            feed.edit_post(request.user, _int(pk), request.data.get('text'), request.data.get('visibility'))
            return Response(self._payload(feed.get_post(request.user, _int(pk))))
        except services.SocialError as error:
            return self.refuse(error)

    def destroy(self, request, pk=None):
        from . import feed
        try:
            feed.delete_post(request.user, _int(pk))
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': 'Публикация удалена'})

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        from . import feed
        liked = str(request.data.get('liked', 'true')).lower() in ('1', 'true')
        try:
            return Response(self._payload(feed.set_like(request.user, _int(pk), liked)))
        except services.SocialError as error:
            return self.refuse(error)

    @action(detail=True, methods=['get', 'post'])
    def comments(self, request, pk=None):
        from . import feed
        from .serializers import comment_payload
        try:
            if request.method == 'POST':
                comment = feed.add_comment(request.user, _int(pk), request.data.get('text', ''))
                return Response(comment_payload(comment, request.user), status=status.HTTP_201_CREATED)
            items, next_before = feed.comments_of(
                request.user, _int(pk), _int(request.query_params.get('before')),
            )
        except services.SocialError as error:
            return self.refuse(error)
        # Комментарии приходят новыми вперёд (по курсору), показываются по порядку
        return Response({'results': [comment_payload(c, request.user) for c in reversed(items)],
                         'next_before': next_before})

    @action(detail=True, methods=['delete'], url_path=r'comments/(?P<comment_id>[0-9]+)')
    def delete_comment(self, request, pk=None, comment_id=None):
        from . import feed
        try:
            feed.delete_comment(request.user, _int(pk), _int(comment_id))
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': 'Комментарий удалён'})

    @action(detail=True, methods=['post'])
    def report(self, request, pk=None):
        from . import feed
        try:
            text = feed.report(
                request.user, _int(pk), request.data.get('reason', ''),
                comment_id=_int(request.data.get('comment_id')),
                text=request.data.get('comment', ''),
                also_block=str(request.data.get('block', '')).lower() in ('1', 'true'),
            )
        except services.SocialError as error:
            return self.refuse(error)
        return Response({'message': text}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def image(self, request, pk=None):
        from . import feed
        try:
            post = feed.get_post(request.user, _int(pk))
        except services.SocialError as error:
            return self.refuse(error)
        if not post.image or (post.is_hidden and post.author_id != request.user.pk):
            return Response({'error': 'Изображения нет'}, status=status.HTTP_404_NOT_FOUND)
        return _file_response(post.image)
