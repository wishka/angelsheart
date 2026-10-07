from django.contrib import admin

from .models import (
    Chat, ChatMember, ChatMessage, Community, CommunityMembership, Interest, SocialProfile,
)


@admin.register(Interest)
class InterestAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'position']
    list_editable = ['position']


@admin.register(SocialProfile)
class SocialProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'display_name', 'city', 'is_discoverable', 'updated_at']
    list_filter = ['is_discoverable', 'gender']
    search_fields = ['user__username', 'display_name', 'city']
    filter_horizontal = ['interests']


class ChatMemberInline(admin.TabularInline):
    model = ChatMember
    extra = 0
    raw_id_fields = ['user']


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    # Текст переписки в админке не показывается: читать чужие сообщения
    # без повода (жалобы, запроса органов) нельзя, а список сообщений
    # на странице чата делал бы именно это
    list_display = ['id', 'kind', 'title', 'created_at', 'last_activity_at']
    list_filter = ['kind']
    inlines = [ChatMemberInline]


class MembershipInline(admin.TabularInline):
    model = CommunityMembership
    extra = 0
    raw_id_fields = ['user']


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ['name', 'topic', 'is_private', 'owner', 'created_at']
    list_filter = ['is_private', 'topic']
    search_fields = ['name']
    raw_id_fields = ['owner', 'chat']
    inlines = [MembershipInline]


from django.contrib import messages as admin_messages  # noqa: E402

from . import services  # noqa: E402
from .models import MessageReport, UserBlock  # noqa: E402


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ['blocker', 'blocked', 'created_at']
    search_fields = ['blocker__username', 'blocked__username']
    raw_id_fields = ['blocker', 'blocked']


@admin.register(MessageReport)
class MessageReportAdmin(admin.ModelAdmin):
    """
    Очередь жалоб.

    Решение принимается по сообщению целиком: «принять» скрывает
    сообщение и закрывает все новые жалобы на него, «отклонить»
    закрывает их и возвращает сообщение, если его скрыло автоматическое
    правило (REPORTS_TO_HIDE жалоб). Ограничить учётную запись автора
    можно в разделе ограничений — там же уходит уведомление с причиной.
    """

    list_display = ['id', 'created_at', 'reason', 'status', 'sender', 'reporter', 'short_text']
    list_filter = ['status', 'reason']
    search_fields = ['sender__username', 'reporter__username', 'message_text']
    readonly_fields = [
        'message', 'reporter', 'reason', 'comment', 'message_text', 'sender',
        'created_at', 'resolved_at', 'resolved_by',
    ]
    fields = readonly_fields + ['status']
    actions = ['accept_reports', 'reject_reports']

    @admin.display(description='Текст')
    def short_text(self, report):
        return report.message_text[:80]

    def _resolve(self, request, queryset, accept):
        messages_done = set()
        for report in queryset.select_related('message'):
            if report.message is None or report.message_id in messages_done:
                continue
            services.resolve_reports(report.message, request.user, accept)
            messages_done.add(report.message_id)
        self.message_user(
            request,
            f'Обработано сообщений: {len(messages_done)}',
            level=admin_messages.SUCCESS,
        )

    @admin.action(description='Принять: скрыть сообщение')
    def accept_reports(self, request, queryset):
        self._resolve(request, queryset, accept=True)

    @admin.action(description='Отклонить: оставить сообщение')
    def reject_reports(self, request, queryset):
        self._resolve(request, queryset, accept=False)
