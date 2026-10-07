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
