"""
API сообщества: /api/profile/, /api/people/, /api/chats/, /api/groups/.

Отдельная ставка ограничения запросов ('social'): открытый чат опрашивает
сервер каждые несколько секунд, и общий лимит 1000 запросов в сутки
кончался бы за час переписки — после чего вставали бы и переводы.
"""

from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import Chat, ChatMessage, Community, CommunityMembership, Interest
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
        return self.get_paginated_response(PersonSerializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        user = services.discoverable_users().filter(pk=_int(pk)).first()
        if user is None and _int(pk) == request.user.pk:
            user = request.user
        if user is None:
            return Response({'error': 'Пользователь не найден или скрыл анкету'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(PersonSerializer(user).data)


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
                message = services.send_message(chat, request.user, serializer.validated_data['text'])
            except services.SocialError as error:
                return self.refuse(error)
            return Response(message_payload(message, request.user), status=status.HTTP_201_CREATED)

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

        return Response({
            'results': [message_payload(m, request.user) for m in batch],
            'has_more': has_more,
        })

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
