from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db import models
from django.contrib.admin import AdminSite
from django.utils.safestring import mark_safe
from django.db.models import Sum, Count
from .models import (
    Balance, Transaction, UserConsent, ConsentLog,
    Fundraise, Donation, CommissionTransaction, WithdrawalRequest
)

# ==================== НАСТРОЙКИ ОБЩЕЙ АДМИНКИ ====================

admin.site.site_header = 'Ангел-Хранитель - Административная панель'
admin.site.site_title = 'Ангел-Хранитель Admin'
admin.site.index_title = 'Управление платформой взаимопомощи'


class CustomAdminSite(AdminSite):
    site_header = 'Ангел-Хранитель - Административная панель'
    
    def get_app_list(self, request):
        app_list = super().get_app_list(request)
        
        # Добавляем кастомные разделы
        custom_sections = [
            {
                'name': 'Финансовые операции',
                'app_label': 'finance',
                'models': [
                    {
                        'name': 'Заявки на вывод',
                        'object_name': 'WithdrawalRequest',
                        'admin_url': '/admin/main/withdrawalrequest/',
                        'view_only': False,
                    },
                    {
                        'name': 'Транзакции',
                        'object_name': 'Transaction',
                        'admin_url': '/admin/main/transaction/',
                        'view_only': False,
                    },
                    {
                        'name': 'Сборы',
                        'object_name': 'Fundraise',
                        'admin_url': '/admin/main/fundraise/',
                        'view_only': False,
                    },
                ]
            }
        ]
        
        # Добавляем кастомные разделы в начало
        app_list = custom_sections + app_list
        return app_list

# ==================== КАСТОМНЫЙ ПОЛЬЗОВАТЕЛЬ ====================

class BalanceInline(admin.StackedInline):
    """Баланс пользователя в карточке пользователя"""
    model = Balance
    can_delete = False
    verbose_name = 'Баланс'
    verbose_name_plural = 'Балансы'
    fields = ['amount']
    readonly_fields = ['amount_display']
    
    def amount_display(self, obj):
        return mark_safe(f'<span style="font-size: 14px; font-weight: bold; color: #28a745;">{obj.amount} ₽</span>')
    
    amount_display.short_description = 'Текущий баланс'


class UserConsentInline(admin.TabularInline):
    """Согласия пользователя"""
    model = UserConsent
    extra = 0
    fields = ['consent_type', 'version', 'is_accepted', 'agreed_at', 'ip_address']
    readonly_fields = ['agreed_at', 'ip_address', 'user_agent']
    can_delete = False
    show_change_link = True


class CustomUserAdmin(BaseUserAdmin):
    """Расширенная админка пользователя"""
    inlines = [BalanceInline, UserConsentInline]
    list_display = ['username', 'email', 'first_name', 'last_name', 'balance_display', 'is_staff', 'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']
    
    def balance_display(self, obj):
        try:
            balance = obj.balance.amount
            color = '#28a745' if balance >= 0 else '#dc3545'
            return mark_safe(f'<span style="color: {color}; font-weight: bold;">{balance} ₽</span>')
        except Balance.DoesNotExist:
            return mark_safe('<span style="color: #b89a9a;">0 ₽</span>')
    
    balance_display.short_description = 'Баланс'
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Финансовая информация', {
            'fields': ('balance_display',),
            'classes': ('collapse',),
        }),
    )
    readonly_fields = ['balance_display']


# Заменяем стандартную админку пользователя
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# ==================== БАЛАНСЫ ====================

@admin.register(Balance)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ['user_link', 'amount_display', 'last_transaction_date']
    list_filter = ['amount']
    search_fields = ['user__username', 'user__email']
    ordering = ['-amount']
    readonly_fields = ['user_link', 'amount_display']
    
    def user_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.user.id}/change/">{obj.user.username}</a>')
    
    user_link.short_description = 'Пользователь'
    
    def amount_display(self, obj):
        if obj.amount > 0:
            return mark_safe(f'<span style="color: #28a745; font-weight: bold;">{obj.amount} ₽</span>')
        elif obj.amount < 0:
            return mark_safe(f'<span style="color: #dc3545; font-weight: bold;">{obj.amount} ₽</span>')
        return mark_safe(f'<span style="color: #b89a9a;">{obj.amount} ₽</span>')
    
    amount_display.short_description = 'Сумма'
    
    def last_transaction_date(self, obj):
        from .models import Transaction
        last_tx = Transaction.objects.filter(
            models.Q(sender=obj.user) | models.Q(receiver=obj.user)
        ).order_by('-created_at').first()
        return last_tx.created_at if last_tx else '—'
    
    last_transaction_date.short_description = 'Последняя транзакция'
    
    fieldsets = (
        ('Информация о пользователе', {
            'fields': ('user_link',)
        }),
        ('Баланс', {
            'fields': ('amount_display',),
            'classes': ('wide',),
        }),
        ('Статистика', {
            'fields': ('last_transaction_date',),
            'classes': ('collapse',),
        }),
    )


# ==================== ТРАНЗАКЦИИ ====================

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'sender_link', 'receiver_link', 'amount_display', 'status_badge', 'created_at', 'is_donation']
    list_filter = ['status', 'is_donation', 'created_at']
    search_fields = ['sender__username', 'receiver__username', 'comment']
    ordering = ['-created_at']
    readonly_fields = ['id', 'created_at']
    list_per_page = 50
    
    def sender_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.sender.id}/change/">{obj.sender.username}</a>')
    
    sender_link.short_description = 'Отправитель'
    
    def receiver_link(self, obj):
        if obj.receiver:
            return mark_safe(f'<a href="/admin/auth/user/{obj.receiver.id}/change/">{obj.receiver.username}</a>')
        return 'Система (комиссия)'
    
    receiver_link.short_description = 'Получатель'
    
    def amount_display(self, obj):
        if obj.is_donation:
            return mark_safe(f'<span style="color: #d4737a;">{obj.amount} ₽</span>')
        return mark_safe(f'<span style="font-weight: bold;">{obj.amount} ₽</span>')
    
    amount_display.short_description = 'Сумма'
    
    def status_badge(self, obj):
        status_colors = {
            'completed': '#28a745',
            'pending': '#ffc107',
            'failed': '#dc3545',
        }
        status_text = {
            'completed': '✅ Завершена',
            'pending': '⏳ Ожидает',
            'failed': '❌ Ошибка',
        }
        color = status_colors.get(obj.status, '#6c757d')
        text = status_text.get(obj.status, obj.status)
        return mark_safe(
            f'<span style="background: {color}; color: white; padding: 2px 8px; border-radius: 12px;">{text}</span>')
    
    status_badge.short_description = 'Статус'
    
    fieldsets = (
        ('Участники транзакции', {
            'fields': ('sender_link', 'receiver_link')
        }),
        ('Детали', {
            'fields': ('amount_display', 'comment', 'status_badge', 'is_donation')
        }),
        ('Информация о сборе', {
            'fields': ('fundraise_id',),
            'classes': ('collapse',),
        }),
        ('Временные метки', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )
    
    actions = ['mark_as_completed', 'mark_as_failed']
    
    @admin.action(description='Отметить выбранные транзакции как завершённые')
    def mark_as_completed(self, request, queryset):
        updated = queryset.update(status='completed')
        self.message_user(request, f'{updated} транзакций помечены как завершённые.')
    
    @admin.action(description='Отметить выбранные транзакции как ошибочные')
    def mark_as_failed(self, request, queryset):
        updated = queryset.update(status='failed')
        self.message_user(request, f'{updated} транзакций помечены как ошибочные.')


# ==================== СБОРЫ СРЕДСТВ ====================

@admin.register(Fundraise)
class FundraiseAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'author_link', 'progress_bar', 'status_badge', 'donors_count', 'created_at']
    list_filter = ['status', 'category', 'created_at', 'is_featured']
    search_fields = ['title', 'description', 'author__username']
    ordering = ['-created_at']
    readonly_fields = ['current_amount', 'donors_count', 'total_commission', 'progress_percent']
    list_per_page = 30
    date_hierarchy = 'created_at'
    
    def author_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.author.id}/change/">{obj.author.username}</a>')
    
    author_link.short_description = 'Автор'
    
    def progress_bar(self, obj):
        percent = obj.get_progress_percent()
        return mark_safe(
            f'''
            <div style="width: 150px; background: #f0e0e0; border-radius: 10px; overflow: hidden;">
                <div style="background: linear-gradient(90deg, #d4737a, #e8a4aa); width: {percent}%; height: 8px;"></div>
            </div>
            <span style="font-size: 11px;">{percent}% ({int(obj.current_amount)} / {int(obj.target_amount)})</span>
            '''
        )
    
    progress_bar.short_description = 'Прогресс'
    
    def status_badge(self, obj):
        status_colors = {
            'active': '#28a745',
            'completed': '#17a2b8',
            'cancelled': '#dc3545',
        }
        status_text = {
            'active': 'Активный',
            'completed': 'Завершен',
            'cancelled': 'Отменен',
        }
        color = status_colors.get(obj.status, '#6c757d')
        text = status_text.get(obj.status, obj.status)
        return mark_safe(
            f'<span style="background: {color}; color: white; padding: 2px 8px; border-radius: 12px;">{text}</span>')
    
    status_badge.short_description = 'Статус'
    
    def progress_percent(self, obj):
        return f"{obj.get_progress_percent()}%"
    
    progress_percent.short_description = 'Процент выполнения'
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('title', 'description', 'category', 'author_link')
        }),
        ('Финансы', {
            'fields': ('target_amount', 'current_amount', 'total_commission', 'progress_percent', 'donors_count')
        }),
        ('Статус и даты', {
            'fields': ('status', 'end_date', 'created_at')
        }),
        ('Внешний вид', {
            'fields': ('image_url', 'is_featured'),
            'classes': ('collapse',),
        }),
    )
    
    actions = ['activate_fundraises', 'complete_fundraises', 'cancel_fundraises', 'feature_fundraises']
    
    @admin.action(description='Активировать выбранные сборы')
    def activate_fundraises(self, request, queryset):
        updated = queryset.update(status='active')
        self.message_user(request, f'{updated} сборов активированы.')
    
    @admin.action(description='Завершить выбранные сборы')
    def complete_fundraises(self, request, queryset):
        updated = queryset.update(status='completed')
        self.message_user(request, f'{updated} сборов завершены.')
    
    @admin.action(description='Отменить выбранные сборы')
    def cancel_fundraises(self, request, queryset):
        updated = queryset.update(status='cancelled')
        self.message_user(request, f'{updated} сборов отменены.')
    
    @admin.action(description='Сделать рекомендуемыми')
    def feature_fundraises(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f'{updated} сборов отмечены как рекомендуемые.')


# ==================== ДОНАТЫ ====================

@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ['id', 'donor_link', 'fundraise_link', 'amount', 'created_at', 'is_anonymous']
    list_filter = ['is_anonymous', 'created_at']
    search_fields = ['donor__username', 'fundraise__title', 'message']
    ordering = ['-created_at']
    readonly_fields = ['created_at']
    list_per_page = 50
    
    def donor_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.donor.id}/change/">{obj.donor.username}</a>')
    
    donor_link.short_description = 'Донатер'
    
    def fundraise_link(self, obj):
        return mark_safe(f'<a href="/admin/main/fundraise/{obj.fundraise.id}/change/">{obj.fundraise.title}</a>')
    
    fundraise_link.short_description = 'Сбор'
    
    fieldsets = (
        ('Участники', {
            'fields': ('donor_link', 'fundraise_link')
        }),
        ('Детали', {
            'fields': ('amount', 'message', 'is_anonymous')
        }),
        ('Временные метки', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )


# ==================== СОГЛАСИЯ ====================

@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'consent_type_display', 'version', 'accepted_badge', 'agreed_at', 'ip_address']
    list_filter = ['consent_type', 'version', 'is_accepted', 'agreed_at']
    search_fields = ['user__username', 'ip_address']
    ordering = ['-agreed_at']
    readonly_fields = ['agreed_at', 'ip_address', 'user_agent', 'revoked_at']
    list_per_page = 50
    
    def user_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.user.id}/change/">{obj.user.username}</a>')
    
    user_link.short_description = 'Пользователь'
    
    def consent_type_display(self, obj):
        return obj.get_consent_type_display()
    
    consent_type_display.short_description = 'Тип согласия'
    
    def accepted_badge(self, obj):
        if obj.is_accepted and not obj.revoked_at:
            return mark_safe('<span style="color: #28a745;">✅ Принято</span>')
        elif obj.revoked_at:
            return mark_safe('<span style="color: #dc3545;">❌ Отозвано</span>')
        return mark_safe('<span style="color: #ffc107;">⏳ Ожидает</span>')
    
    accepted_badge.short_description = 'Статус'
    
    fieldsets = (
        ('Пользователь', {
            'fields': ('user_link',)
        }),
        ('Согласие', {
            'fields': ('consent_type_display', 'version', 'accepted_badge')
        }),
        ('Техническая информация', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',),
        }),
        ('Временные метки', {
            'fields': ('agreed_at', 'revoked_at', 'revocation_reason'),
            'classes': ('collapse',),
        }),
    )


# ==================== ЛОГИ СОГЛАСИЙ ====================

@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'action_badge', 'consent_type_display', 'version', 'created_at', 'ip_address']
    list_filter = ['action', 'consent_type', 'version', 'created_at']
    search_fields = ['user__username', 'ip_address']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'ip_address', 'user_agent']
    list_per_page = 100
    
    def user_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.user.id}/change/">{obj.user.username}</a>')
    
    user_link.short_description = 'Пользователь'
    
    def consent_type_display(self, obj):
        return dict(UserConsent.CONSENT_TYPES).get(obj.consent_type, obj.consent_type)
    
    consent_type_display.short_description = 'Тип согласия'
    
    def action_badge(self, obj):
        if obj.action == 'accept':
            return mark_safe('<span style="color: #28a745;">✅ Принятие</span>')
        return mark_safe('<span style="color: #dc3545;">❌ Отзыв</span>')
    
    action_badge.short_description = 'Действие'
    
    fieldsets = (
        ('Пользователь', {
            'fields': ('user_link',)
        }),
        ('Действие', {
            'fields': ('action_badge', 'consent_type_display', 'version')
        }),
        ('Техническая информация', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',),
        }),
        ('Временные метки', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )


# ==================== КОМИССИИ ====================

@admin.register(CommissionTransaction)
class CommissionTransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'donation_link', 'amount', 'percent', 'created_at']
    list_filter = ['percent', 'created_at']
    ordering = ['-created_at']
    readonly_fields = ['created_at']
    
    def donation_link(self, obj):
        return mark_safe(
            f'<a href="/admin/main/donation/{obj.donation.id}/change/">Пожертвование #{obj.donation.id}</a>')
    
    donation_link.short_description = 'Пожертвование'
    
    fieldsets = (
        ('Пожертвование', {
            'fields': ('donation_link',)
        }),
        ('Комиссия', {
            'fields': ('amount', 'percent')
        }),
        ('Временные метки', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'amount_display', 'status', 'status_badge', 'payment_method_display',
                    'created_at', 'processed_at']
    list_filter = ['status', 'payment_method', 'created_at']
    search_fields = ['id', 'user__username', 'user__email', 'transaction_id', 'comment']
    readonly_fields = ['created_at', 'payment_details_display']
    list_per_page = 50
    list_editable = ['status']
    
    def user_link(self, obj):
        return mark_safe(f'<a href="/admin/auth/user/{obj.user.id}/change/">{obj.user.username}</a>')
    
    user_link.short_description = 'Пользователь'
    
    def amount_display(self, obj):
        return mark_safe(f'<span style="color: #d4737a; font-weight: bold;">{obj.amount} ₽</span>')
    
    amount_display.short_description = 'Сумма'
    
    def status_badge(self, obj):
        status_colors = {
            'pending': '#ffc107',
            'processing': '#17a2b8',
            'completed': '#28a745',
            'rejected': '#dc3545',
            'cancelled': '#6c757d',
        }
        status_text = {
            'pending': '⏳ На рассмотрении',
            'processing': '🔄 В обработке',
            'completed': '✅ Выполнен',
            'rejected': '❌ Отклонен',
            'cancelled': '🗑️ Отменен',
        }
        color = status_colors.get(obj.status, '#6c757d')
        text = status_text.get(obj.status, obj.status)
        return mark_safe(
            f'<span style="background: {color}; color: white; padding: 2px 8px; border-radius: 12px;">{text}</span>')
    
    status_badge.short_description = 'Статус'
    
    def payment_method_display(self, obj):
        methods = {
            'card': '💳 Банковская карта',
            'sbp': '📱 СБП',
            'yoomoney': '💰 ЮMoney',
            'crypto': '🪙 Криптовалюта',
        }
        return methods.get(obj.payment_method, obj.payment_method)
    
    payment_method_display.short_description = 'Способ вывода'
    
    def payment_details_display(self, obj):
        details = []
        for key, value in obj.payment_details.items():
            if 'card_number' in key and value:
                masked = f'****{str(value)[-4:]}'
                details.append(f'💳 Номер карты: {masked}')
            elif 'card_holder' in key and value:
                details.append(f'👤 Владелец: {value}')
            elif 'expiry_date' in key and value:
                details.append(f'📅 Срок: {value}')
            elif 'phone' in key and value:
                phone = str(value)
                masked = phone[:4] + '***' + phone[-4:] if len(phone) > 7 else '***'
                details.append(f'📱 Телефон: {masked}')
            elif 'wallet' in key and value:
                masked = '***' + str(value)[-8:]
                details.append(f'💰 Кошелек: {masked}')
            else:
                details.append(f'📝 {key}: {value}')
        return mark_safe('<br>'.join(details) if details else '—')
    
    payment_details_display.short_description = 'Реквизиты'
    
    actions = ['approve_withdrawals', 'complete_withdrawals', 'reject_withdrawals']
    
    @admin.action(description='✅ Подтвердить выбранные заявки')
    def approve_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='pending'):
            withdrawal.approve(request.user)
        self.message_user(request, f'{queryset.count()} заявок подтверждены')
    
    @admin.action(description='💸 Завершить выбранные заявки')
    def complete_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='processing'):
            withdrawal.complete(request.user)
        self.message_user(request, f'{queryset.count()} заявок завершены')
    
    @admin.action(description='❌ Отклонить выбранные заявки')
    def reject_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='pending'):
            withdrawal.reject(request.user, 'Отклонено администратором')
        self.message_user(request, f'{queryset.count()} заявок отклонены')
    
    fieldsets = (
        ('Заявитель', {
            'fields': ('user_link',)
        }),
        ('Сумма и статус', {
            'fields': ('amount_display', 'status_badge', 'comment')
        }),
        ('Реквизиты', {
            'fields': ('payment_method_display', 'payment_details_display')
        }),
        ('Информация о транзакции', {
            'fields': ('transaction_id',),
            'classes': ('collapse',),
        }),
        ('Временные метки', {
            'fields': ('created_at', 'processed_at', 'processed_by'),
            'classes': ('collapse',),
        }),
    )
    
    payment_details_display.short_description = 'Реквизиты'
    
    actions = ['approve_withdrawals', 'complete_withdrawals', 'reject_withdrawals']
    
    @admin.action(description='Подтвердить выбранные заявки')
    def approve_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='pending'):
            withdrawal.approve(request.user)
        self.message_user(request, f'{queryset.count()} заявок подтверждены')
    
    @admin.action(description='Завершить выбранные заявки')
    def complete_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='processing'):
            withdrawal.complete(request.user)
        self.message_user(request, f'{queryset.count()} заявок завершены')
    
    @admin.action(description='Отклонить выбранные заявки')
    def reject_withdrawals(self, request, queryset):
        for withdrawal in queryset.filter(status='pending'):
            withdrawal.reject(request.user, 'Отклонено администратором')
        self.message_user(request, f'{queryset.count()} заявок отклонены')
    
    fieldsets = (
        ('Заявитель', {
            'fields': ('user_link',)
        }),
        ('Сумма и статус', {
            'fields': ('amount_display', 'status_badge', 'comment')
        }),
        ('Реквизиты', {
            'fields': ('payment_method', 'payment_details_display')
        }),
        ('Информация о транзакции', {
            'fields': ('transaction_id',),
            'classes': ('collapse',),
        }),
        ('Временные метки', {
            'fields': ('created_at', 'processed_at', 'processed_by'),
            'classes': ('collapse',),
        }),
    )


def get_admin_stats():
    """Статистика для главной страницы админки"""
    from django.db.models import Sum
    
    return {
        'total_withdrawals_pending': WithdrawalRequest.objects.filter(status='pending').count(),
        'total_withdrawals_processing': WithdrawalRequest.objects.filter(status='processing').count(),
        'total_withdrawals_completed': WithdrawalRequest.objects.filter(status='completed').count(),
        'total_withdrawals_rejected': WithdrawalRequest.objects.filter(status='rejected').count(),
        'total_withdrawals_amount': WithdrawalRequest.objects.filter(
            status__in=['pending', 'processing', 'completed']
        ).aggregate(Sum('amount'))['amount__sum'] or 0,
    }