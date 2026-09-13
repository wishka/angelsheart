from decimal import Decimal

from django.contrib import admin
from django.urls import reverse
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db import models
from django.contrib.admin import AdminSite
from django.utils.html import format_html, format_html_join
from django.db.models import Sum, Count
from .models import (
    Balance, Transaction, UserConsent, ConsentLog,
    AccountRestriction, DataBreachIncident, Fundraise, FundraiseDocument, Donation,
    WithdrawalRequest,
    PaymentTransaction, TwoFactorAuth, SecurityLog, UserVerification, KYCDocument,
    PersonalDataAccessLog,
)

# ==================== НАСТРОЙКИ ОБЩЕЙ АДМИНКИ ====================

admin.site.site_header = 'Ангел-Хранитель - Административная панель'
admin.site.site_title = 'Ангел-Хранитель Admin'
admin.site.index_title = 'Управление платформой взаимопомощи'


class CustomAdminSite(AdminSite):
    site_header = 'Ангел-Хранитель - Административная панель'
    
    def get_app_list(self, request):
        app_list = super().get_app_list(request)
        
        custom_sections = [
            {
                'name': 'Финансовые операции',
                'app_label': 'finance',
                'models': [
                    {'name': 'Заявки на вывод', 'object_name': 'WithdrawalRequest',
                     'admin_url': '/admin/main/withdrawalrequest/', 'view_only': False},
                    {'name': 'Транзакции', 'object_name': 'Transaction',
                     'admin_url': '/admin/main/transaction/', 'view_only': False},
                    {'name': 'Сборы', 'object_name': 'Fundraise',
                     'admin_url': '/admin/main/fundraise/', 'view_only': False},
                    {'name': 'Платежи', 'object_name': 'PaymentTransaction',
                     'admin_url': '/admin/main/paymenttransaction/', 'view_only': False},
                ]
            },
            {
                'name': 'Безопасность',
                'app_label': 'security',
                'models': [
                    {'name': 'Логи безопасности', 'object_name': 'SecurityLog',
                     'admin_url': '/admin/main/securitylog/', 'view_only': False},
                    {'name': '2FA', 'object_name': 'TwoFactorAuth',
                     'admin_url': '/admin/main/twofactorauth/', 'view_only': False},
                    {'name': 'Верификация', 'object_name': 'UserVerification',
                     'admin_url': '/admin/main/userverification/', 'view_only': False},
                    {'name': 'KYC документы', 'object_name': 'KYCDocument',
                     'admin_url': '/admin/main/kycdocument/', 'view_only': False},
                ]
            }
        ]
        
        app_list = custom_sections + app_list
        return app_list


# ==================== КАСТОМНЫЙ ПОЛЬЗОВАТЕЛЬ ====================

class BalanceInline(admin.StackedInline):
    model = Balance
    can_delete = False
    verbose_name = 'Баланс'
    verbose_name_plural = 'Балансы'
    fields = ['amount']
    readonly_fields = ['amount_display']
    
    def amount_display(self, obj):
        return format_html('<span style="font-size: 14px; font-weight: bold; color: #28a745;">{} ₽</span>', obj.amount)
    
    amount_display.short_description = 'Текущий баланс'


class UserConsentInline(admin.TabularInline):
    model = UserConsent
    extra = 0
    fields = ['consent_type', 'version', 'is_accepted', 'agreed_at', 'ip_address']
    readonly_fields = ['agreed_at', 'ip_address', 'user_agent']
    can_delete = False
    show_change_link = True


class CustomUserAdmin(BaseUserAdmin):
    inlines = [BalanceInline, UserConsentInline]
    list_display = ['username', 'email', 'first_name', 'last_name', 'balance_display', 'verification_level', 'is_staff',
                    'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active', 'date_joined']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']
    
    def balance_display(self, obj):
        try:
            balance = obj.balance.amount
            color = '#28a745' if balance >= 0 else '#dc3545'
            return format_html('<span style="color: {}; font-weight: bold;">{} ₽</span>', color, balance)
        except Balance.DoesNotExist:
            return format_html('<span style="color: #b89a9a;">0 ₽</span>')
    
    balance_display.short_description = 'Баланс'
    
    def verification_level(self, obj):
        try:
            level = obj.verification.level
            levels = {'unverified': '🔴 Не верифицирован', 'basic': '🟡 Базовая', 'full': '🟢 Полная'}
            return levels.get(level, level)
        except UserVerification.DoesNotExist:
            return '🔴 Не верифицирован'
    
    verification_level.short_description = 'Верификация'
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Финансовая информация', {'fields': ('balance_display',), 'classes': ('collapse',)}),
        ('Верификация', {'fields': ('verification_level',), 'classes': ('collapse',)}),
    )
    readonly_fields = ['balance_display', 'verification_level']


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
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def amount_display(self, obj):
        if obj.amount > 0:
            return format_html('<span style="color: #28a745; font-weight: bold;">{} ₽</span>', obj.amount)
        elif obj.amount < 0:
            return format_html('<span style="color: #dc3545; font-weight: bold;">{} ₽</span>', obj.amount)
        return format_html('<span style="color: #b89a9a;">{} ₽</span>', obj.amount)
    
    amount_display.short_description = 'Сумма'
    
    def last_transaction_date(self, obj):
        from .models import Transaction
        last_tx = Transaction.objects.filter(
            models.Q(sender=obj.user) | models.Q(receiver=obj.user)
        ).order_by('-created_at').first()
        return last_tx.created_at if last_tx else '—'
    
    last_transaction_date.short_description = 'Последняя транзакция'


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
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.sender.id, obj.sender.username)
    
    sender_link.short_description = 'Отправитель'
    
    def receiver_link(self, obj):
        if obj.receiver:
            return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.receiver.id, obj.receiver.username)
        return 'Система (комиссия)'
    
    receiver_link.short_description = 'Получатель'
    
    def amount_display(self, obj):
        if obj.is_donation:
            return format_html('<span style="color: #d4737a;">{} ₽</span>', obj.amount)
        return format_html('<span style="font-weight: bold;">{} ₽</span>', obj.amount)
    
    amount_display.short_description = 'Сумма'
    
    def status_badge(self, obj):
        colors = {'completed': '#28a745', 'pending': '#ffc107', 'failed': '#dc3545'}
        texts = {'completed': '✅ Завершена', 'pending': '⏳ Ожидает', 'failed': '❌ Ошибка'}
        color = colors.get(obj.status, '#6c757d')
        text = texts.get(obj.status, obj.status)
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">{}</span>',
            color, text)
    
    status_badge.short_description = 'Статус'
    
    # Массовые действия «пометить завершённой / ошибочной» удалены: они
    # меняли статус проводок, не трогая балансы, из-за чего история
    # расходилась с остатками и сверка становилась невозможной.
    # Транзакция — журнал уже случившегося, задним числом её не правят.
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ==================== СБОРЫ СРЕДСТВ ====================

@admin.register(Fundraise)
class FundraiseAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'author_link', 'progress_bar', 'status_badge',
                    'moderation_badge', 'donors_count', 'created_at']
    list_filter = ['moderation_status', 'status', 'category', 'created_at', 'is_featured']
    search_fields = ['title', 'description', 'author__username']
    ordering = ['-created_at']
    # Результат проверки правится только на странице модерации: там решение
    # фиксируется вместе с тем, кто его принял, и с комментарием автору.
    # Правка поля прямо в админке оставила бы одобрение без автора.
    readonly_fields = ['current_amount', 'donors_count', 'progress_percent',
                       'moderation_status', 'submitted_at', 'moderated_at', 'moderated_by',
                       'moderation_link']
    list_per_page = 30
    date_hierarchy = 'created_at'
    
    def author_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.author.id, obj.author.username)
    
    author_link.short_description = 'Автор'
    
    def progress_bar(self, obj):
        percent = obj.get_progress_percent()
        return format_html(
            '<div style="width: 150px; background: #f0e0e0; border-radius: 10px; overflow: hidden;">'
            '<div style="background: linear-gradient(90deg, #d4737a, #e8a4aa); '
            'width: {}%; height: 8px;"></div></div>'
            '<span style="font-size: 11px;">{}% ({} / {})</span>',
            min(percent, 100), percent, int(obj.current_amount), int(obj.target_amount))
    
    progress_bar.short_description = 'Прогресс'
    
    def status_badge(self, obj):
        colors = {'active': '#28a745', 'completed': '#17a2b8', 'cancelled': '#dc3545'}
        texts = {'active': 'Активный', 'completed': 'Завершен', 'cancelled': 'Отменен'}
        color = colors.get(obj.status, '#6c757d')
        text = texts.get(obj.status, obj.status)
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">{}</span>',
            color, text)
    
    status_badge.short_description = 'Статус'
    
    def progress_percent(self, obj):
        return f"{obj.get_progress_percent()}%"

    progress_percent.short_description = 'Процент выполнения'

    def moderation_badge(self, obj):
        colors = {
            'draft': '#6c757d', 'pending': '#ffc107', 'approved': '#28a745',
            'changes_requested': '#fd7e14', 'rejected': '#dc3545',
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">{}</span>',
            colors.get(obj.moderation_status, '#6c757d'), obj.get_moderation_status_display())

    moderation_badge.short_description = 'Проверка'

    def moderation_link(self, obj):
        if not obj.pk:
            return '—'
        return format_html(
            '<a href="{}" target="_blank">Открыть страницу проверки</a>',
            reverse('main:moderate_fundraise', args=[obj.pk]))

    moderation_link.short_description = 'Модерация'

    # Действие «Активировать» удалено: реактивация отменённого сбора вернула бы
    # к жизни сбор, деньги по которому уже возвращены донорам.
    actions = ['complete_fundraises', 'cancel_fundraises', 'feature_fundraises']

    @admin.action(description='Завершить выбранные сборы')
    def complete_fundraises(self, request, queryset):
        # Через save(), а не queryset.update(): update обходит модель
        # и не проставляет closed_at, от которого считается срок хранения
        # приложенных документов.
        updated = 0
        for fundraise in queryset.filter(status='active', moderation_status='approved'):
            fundraise.status = 'completed'
            fundraise.save(update_fields=['status'])
            updated += 1
        self.message_user(request, f'Завершено сборов: {updated}.')

    @admin.action(description='❌ Отменить с возвратом средств донорам')
    def cancel_fundraises(self, request, queryset):
        """
        Отмена сбора администратором с возвратом пожертвований.

        Раньше действие делало queryset.update(status='cancelled') — то есть
        обходило всю логику возврата, и деньги оставались у автора. Это ровно
        та дыра, которую закрыли во вью cancel_fundraise, но админский путь
        остался открытым и противоречил пп. 7.3 и 7.6 оферты.
        """
        from django.db import transaction as db_transaction

        from main import services

        cancelled = 0
        refunded_total = Decimal('0')
        shortfall_total = Decimal('0')
        failed = []

        for fundraise in queryset.filter(status='active'):
            try:
                with db_transaction.atomic():
                    result = services.refund_donations(
                        fundraise, reason='сбор отменён администратором',
                    )
                    fundraise.status = 'cancelled'
                    fundraise.save(update_fields=['status'])
                cancelled += 1
                refunded_total += result['refunded']
                shortfall_total += result['shortfall']
            except Exception as exc:
                failed.append(f'#{fundraise.pk}: {exc}')

        self.message_user(
            request,
            f'Отменено сборов: {cancelled}. Возвращено донорам: {refunded_total} ₽.',
        )
        if shortfall_total:
            self.message_user(
                request,
                f'Не удалось вернуть {shortfall_total} ₽ — у авторов недостаточно средств. '
                f'Задолженность зафиксирована в истории операций.',
                level='warning',
            )
        if failed:
            self.message_user(request, 'Ошибки: ' + '; '.join(failed), level='error')

    @admin.action(description='Сделать рекомендуемыми')
    def feature_fundraises(self, request, queryset):
        # Рекомендовать можно только проверенный сбор: «рекомендуемый»
        # означает, что площадка за него ручается, и попадание сюда
        # непроверенной заявки было бы хуже обычной публикации.
        approved = queryset.filter(moderation_status='approved')
        skipped = queryset.count() - approved.count()
        updated = approved.update(is_featured=True)
        self.message_user(request, f'{updated} сборов отмечены как рекомендуемые.')
        if skipped:
            self.message_user(
                request,
                f'Пропущено непроверенных сборов: {skipped}.',
                level='warning',
            )


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
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.donor.id, obj.donor.username)
    
    donor_link.short_description = 'Донатер'
    
    def fundraise_link(self, obj):
        return format_html('<a href="/admin/main/fundraise/{}/change/">{}</a>', obj.fundraise.id, obj.fundraise.title)
    
    fundraise_link.short_description = 'Сбор'


# ==================== СОГЛАСИЯ ====================

@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'consent_type_display', 'version', 'accepted_badge', 'agreed_at']
    list_filter = ['consent_type', 'version', 'is_accepted', 'agreed_at']
    search_fields = ['user__username']
    ordering = ['-agreed_at']
    readonly_fields = ['agreed_at', 'ip_address', 'user_agent', 'revoked_at']
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def consent_type_display(self, obj):
        return obj.get_consent_type_display()
    
    consent_type_display.short_description = 'Тип согласия'
    
    def accepted_badge(self, obj):
        if obj.is_accepted and not obj.revoked_at:
            return format_html('<span style="color: #28a745;">✅ Принято</span>')
        elif obj.revoked_at:
            return format_html('<span style="color: #dc3545;">❌ Отозвано</span>')
        return format_html('<span style="color: #ffc107;">⏳ Ожидает</span>')
    
    accepted_badge.short_description = 'Статус'


@admin.register(ConsentLog)
class ConsentLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'action_badge', 'consent_type_display', 'version', 'created_at']
    list_filter = ['action', 'consent_type', 'version', 'created_at']
    search_fields = ['user__username']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'ip_address', 'user_agent']
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def consent_type_display(self, obj):
        return dict(UserConsent.CONSENT_TYPES).get(obj.consent_type, obj.consent_type)
    
    consent_type_display.short_description = 'Тип согласия'
    
    def action_badge(self, obj):
        return format_html('<span style="color: #28a745;">✅ Принятие</span>') if obj.action == 'accept' \
            else format_html('<span style="color: #dc3545;">❌ Отзыв</span>')
    
    action_badge.short_description = 'Действие'


# ==================== ЗАЯВКИ НА ВЫВОД ====================

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'amount_display', 'status', 'status_badge', 'payment_method_display',
                    'created_at']
    list_filter = ['status', 'payment_method', 'created_at']
    search_fields = ['id', 'user__username', 'transaction_id', 'comment']
    readonly_fields = ['created_at', 'payment_details_display', 'payment_details_masked']
    list_per_page = 50
    # list_editable = ['status'] убран намеренно: правка статуса прямо в списке
    # обходила методы reject()/cancel()/mark_failed(), и удержанные деньги
    # не возвращались пользователю. Статус меняется только действиями ниже.
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Открытие заявки показывает платёжные реквизиты — обращение к ПДн.

        Номер карты маскирован, но имя держателя видно целиком, и сам факт,
        кто из сотрудников и когда смотрел чужие реквизиты, иначе
        восстановить нечем.
        """
        withdrawal = self.get_object(request, object_id)
        if withdrawal is not None:
            PersonalDataAccessLog.record(
                actor=request.user,
                subject=withdrawal.user,
                data_type='payment_details',
                reason='Обработка заявки на вывод средств',
                request=request,
                object_repr=f'WithdrawalRequest #{withdrawal.pk}',
            )
        return super().change_view(request, object_id, form_url, extra_context)

    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)

    user_link.short_description = 'Пользователь'

    def amount_display(self, obj):
        return format_html('<span style="color: #d4737a; font-weight: bold;">{} ₽</span>', obj.amount)

    amount_display.short_description = 'Сумма'
    
    def status_badge(self, obj):
        colors = {'pending': '#ffc107', 'processing': '#17a2b8', 'completed': '#28a745',
                  'rejected': '#dc3545', 'cancelled': '#6c757d'}
        texts = {'pending': '⏳ На рассмотрении', 'processing': '🔄 В обработке',
                 'completed': '✅ Выполнен', 'rejected': '❌ Отклонен', 'cancelled': '🗑️ Отменен'}
        color = colors.get(obj.status, '#6c757d')
        text = texts.get(obj.status, obj.status)
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">{}</span>',
            color, text)
    
    status_badge.short_description = 'Статус'
    
    def payment_method_display(self, obj):
        methods = {'card': '💳 Банковская карта', 'sbp': '📱 СБП',
                   'yoomoney': '💰 ЮMoney'}
        return methods.get(obj.payment_method, obj.payment_method)
    
    payment_method_display.short_description = 'Способ вывода'
    
    def payment_details_display(self, obj):
        details = []
        for key, value in obj.payment_details.items():
            if 'card_number' in key:
                details.append(f'💳 Номер карты: ****{str(value)[-4:]}')
            elif 'card_holder' in key:
                details.append(f'👤 Владелец: {value}')
            elif 'phone' in key:
                phone = str(value)
                masked = phone[:4] + '***' + phone[-4:] if len(phone) > 7 else '***'
                details.append(f'📱 Телефон: {masked}')
            elif 'wallet' in key:
                details.append(f'💰 Кошелек: ***{str(value)[-8:]}')
            else:
                details.append(f'📝 {key}: {value}')
        if not details:
            return '—'
        # format_html_join экранирует каждое значение: реквизиты вводит
        # пользователь, и раньше имя держателя карты попадало в HTML сырым
        return format_html_join('', '{}<br>', ((item,) for item in details))
    
    payment_details_display.short_description = 'Реквизиты'
    
    actions = ['approve_withdrawals', 'pay_out_withdrawals', 'reject_withdrawals']

    @admin.action(description='✅ Принять к выплате')
    def approve_withdrawals(self, request, queryset):
        # Сообщения раньше показывали queryset.count() — общее число
        # выделенных строк, а не число реально обработанных заявок
        processed = sum(
            1 for withdrawal in queryset.filter(status='pending')
            if withdrawal.approve(request.user)
        )
        self.message_user(request, f'Принято к выплате заявок: {processed}')

    @admin.action(description='💸 Выполнить выплату')
    def pay_out_withdrawals(self, request, queryset):
        """
        Реальная выплата через платёжную систему.

        Раньше действие «Завершить» просто меняло статус на completed,
        не переводя денег: MassWithdrawalService не вызывался ниоткуда,
        и заявка помечалась выполненной без выплаты.
        """
        from main.payments.withdrawals import MassWithdrawalService

        service = MassWithdrawalService()
        ids = list(queryset.filter(status='processing').values_list('id', flat=True))
        if not ids:
            self.message_user(request, 'Нет заявок в статусе «В обработке»', level='warning')
            return

        results = service.process_mass_withdrawals(ids, admin_user=request.user)
        self.message_user(
            request,
            f'Обработано {results["total"]}: успешно {results["success"]}, '
            f'с ошибкой {results["failed"]}',
            level='info' if results['failed'] == 0 else 'warning',
        )

    @admin.action(description='❌ Отклонить с возвратом средств')
    def reject_withdrawals(self, request, queryset):
        processed = sum(
            1 for withdrawal in queryset.filter(status__in=['pending', 'processing'])
            if withdrawal.reject(request.user, 'Отклонено администратором')
        )
        self.message_user(request, f'Отклонено заявок (средства возвращены): {processed}')


# ==================== НОВЫЕ МОДЕЛИ ====================

@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'amount', 'payment_method', 'status_badge', 'created_at']
    list_filter = ['status', 'payment_method', 'created_at']
    search_fields = ['user__username', 'payment_id']
    readonly_fields = ['created_at', 'paid_at']
    list_per_page = 50
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def status_badge(self, obj):
        colors = {'pending': '#ffc107', 'processing': '#17a2b8', 'paid': '#28a745',
                  'failed': '#dc3545', 'refunded': '#6c757d', 'cancelled': '#6c757d'}
        texts = {'pending': '⏳ Ожидает', 'processing': '🔄 В обработке', 'paid': '✅ Оплачен',
                 'failed': '❌ Ошибка', 'refunded': '↩️ Возвращен', 'cancelled': '🗑️ Отменен'}
        color = colors.get(obj.status, '#6c757d')
        text = texts.get(obj.status, obj.status)
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 12px;">{}</span>',
            color, text)
    
    status_badge.short_description = 'Статус'


@admin.register(TwoFactorAuth)
class TwoFactorAuthAdmin(admin.ModelAdmin):
    list_display = ['user_link', 'is_enabled', 'created_at', 'last_used']
    list_filter = ['is_enabled', 'created_at']
    search_fields = ['user__username']
    readonly_fields = ['secret_key', 'backup_codes']
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def has_add_permission(self, request):
        return False


@admin.register(SecurityLog)
class SecurityLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'action', 'ip_address', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['user__username', 'ip_address']
    readonly_fields = ['created_at', 'ip_address', 'user_agent', 'details']
    
    def user_link(self, obj):
        if not obj.user:
            return format_html('<span>Аноним</span>')
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserVerification)
class UserVerificationAdmin(admin.ModelAdmin):
    list_display = ['user_link', 'level_badge', 'full_name', 'verified_at']
    list_filter = ['level', 'verified_at']
    # Поиск по passport_number невозможен: поле зашифровано (Fernet
    # недетерминирован), и такой поиск молча возвращал бы пустой результат
    search_fields = ['user__username', 'full_name']
    readonly_fields = ['user_link', 'verified_at', 'updated_at']
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def level_badge(self, obj):
        colors = {'unverified': '#dc3545', 'basic': '#ffc107', 'full': '#28a745'}
        texts = {'unverified': '🔴 Не верифицирован', 'basic': '🟡 Базовая', 'full': '🟢 Полная'}
        return format_html(
            '<span style="color: {};">{}</span>',
            colors.get(obj.level, '#6c757d'), texts.get(obj.level, obj.level))
    
    level_badge.short_description = 'Уровень'

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Открытие карточки верификации — это чтение паспортных данных.

        Запись делается до отрисовки страницы: журнал должен фиксировать
        обращение, а не только удачное его завершение.
        """
        verification = self.get_object(request, object_id)
        if verification is not None:
            PersonalDataAccessLog.record(
                actor=request.user,
                subject=verification.user,
                data_type='passport',
                reason='Просмотр карточки верификации в админ-панели',
                request=request,
                object_repr=f'UserVerification #{verification.pk}',
            )
        return super().change_view(request, object_id, form_url, extra_context)

    actions = ['approve_basic', 'approve_full', 'reject_verification']

    def _require_approved_document(self, queryset):
        """
        Уровень верификации выдаётся только при наличии подтверждённого
        документа. Раньше одно действие ставило 'full' пачке пользователей
        без единой проверки — документы можно было вообще не открывать.
        """
        from main.models import KYCDocument

        approved_user_ids = set(
            KYCDocument.objects.filter(status='approved').values_list('user_id', flat=True)
        )
        allowed = [v for v in queryset if v.user_id in approved_user_ids]
        skipped = [v for v in queryset if v.user_id not in approved_user_ids]
        return allowed, skipped

    def _set_level(self, request, queryset, level, title):
        from django.utils import timezone

        allowed, skipped = self._require_approved_document(queryset)
        for verification in allowed:
            verification.level = level
            verification.verified_at = timezone.now()
            verification.save(update_fields=['level', 'verified_at'])

        self.message_user(request, f'{title}: {len(allowed)} пользователей.')
        if skipped:
            self.message_user(
                request,
                f'Пропущено {len(skipped)}: нет подтверждённого документа. '
                f'Сначала проверьте и подтвердите скан в разделе «KYC документы».',
                level='warning',
            )

    @admin.action(description='🟡 Присвоить базовую верификацию')
    def approve_basic(self, request, queryset):
        self._set_level(request, queryset, 'basic', 'Базовая верификация присвоена')

    @admin.action(description='🟢 Присвоить полную верификацию')
    def approve_full(self, request, queryset):
        self._set_level(request, queryset, 'full', 'Полная верификация присвоена')

    @admin.action(description='❌ Отклонить верификацию')
    def reject_verification(self, request, queryset):
        updated = queryset.update(level='unverified', verified_at=None)
        self.message_user(request, f'{updated} пользователей отклонены.')


@admin.register(KYCDocument)
class KYCDocumentAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_link', 'document_type_display', 'status_badge', 'uploaded_at']
    list_filter = ['document_type', 'status', 'uploaded_at']
    # document_number зашифрован — поиск по нему невозможен
    search_fields = ['user__username']
    readonly_fields = ['uploaded_at', 'document_image_preview']
    
    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.username)
    
    user_link.short_description = 'Пользователь'
    
    def document_type_display(self, obj):
        types = {'passport': '📘 Паспорт РФ', 'driver_license': '🚗 Водительское удостоверение',
                 'snils': '🆔 СНИЛС', 'inn': '📄 ИНН'}
        return types.get(obj.document_type, obj.document_type)
    
    document_type_display.short_description = 'Тип документа'
    
    def status_badge(self, obj):
        colors = {'pending': '#ffc107', 'approved': '#28a745', 'rejected': '#dc3545'}
        texts = {'pending': '⏳ На проверке', 'approved': '✅ Подтвержден', 'rejected': '❌ Отклонен'}
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 12px;">{}</span>',
            colors.get(obj.status, '#6c757d'), texts.get(obj.status, obj.status))
    
    status_badge.short_description = 'Статус'
    
    def document_image_preview(self, obj):
        # Прямая ссылка на .url больше недоступна: файлы лежат в приватном
        # хранилище вне MEDIA_ROOT. Просмотр идёт через вью с проверкой прав.
        if not obj.document_image or not obj.pk:
            return 'Нет изображения'
        url = reverse('main:kyc_document', args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank">'
            '<img src="{}" style="max-width: 200px; max-height: 150px;" /></a>',
            url, url)
    
    document_image_preview.short_description = 'Превью'
    
    actions = ['approve_documents', 'reject_documents']
    
    @admin.action(description='✅ Подтвердить выбранные документы')
    def approve_documents(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(status='approved', verified_at=timezone.now(), verified_by=request.user)
        self.message_user(request, f'{updated} документов подтверждены.')
    
    @admin.action(description='❌ Отклонить выбранные документы')
    def reject_documents(self, request, queryset):
        updated = queryset.update(status='rejected')
        self.message_user(request, f'{updated} документов отклонены.')


# ==================== СТАТИСТИКА ====================

def get_admin_stats():
    return {
        'total_withdrawals_pending': WithdrawalRequest.objects.filter(status='pending').count(),
        'total_withdrawals_processing': WithdrawalRequest.objects.filter(status='processing').count(),
        'total_withdrawals_completed': WithdrawalRequest.objects.filter(status='completed').count(),
        'total_withdrawals_rejected': WithdrawalRequest.objects.filter(status='rejected').count(),
        'total_withdrawals_amount': WithdrawalRequest.objects.filter(
            status__in=['pending', 'processing', 'completed']
        ).aggregate(Sum('amount'))['amount__sum'] or 0,
        'total_payments_pending': PaymentTransaction.objects.filter(status='pending').count(),
        'total_unverified_users': UserVerification.objects.filter(level='unverified').count(),
        'total_kyc_pending': KYCDocument.objects.filter(status='pending').count(),
    }

# ==================== ДОКУМЕНТЫ К СБОРАМ ====================

@admin.register(FundraiseDocument)
class FundraiseDocumentAdmin(admin.ModelAdmin):
    """
    Документы к сборам.

    Ссылка ведёт на вью, которая записывает обращение в журнал доступа
    к ПДн. Прямая ссылка на файл здесь не показывается намеренно: хранилище
    приватное, а сам факт открытия справки о диагнозе должен быть
    зафиксирован.
    """

    list_display = ['id', 'fundraise_link', 'document_type', 'uploaded_at', 'download_link']
    list_filter = ['document_type', 'uploaded_at']
    search_fields = ['fundraise__title', 'fundraise__author__username']
    readonly_fields = ['fundraise', 'document_type', 'file', 'comment', 'uploaded_at',
                       'download_link']

    def fundraise_link(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse('main:moderate_fundraise', args=[obj.fundraise_id]),
            obj.fundraise.title)

    fundraise_link.short_description = 'Сбор'

    def download_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank">Скачать (будет записано в журнал)</a>',
            reverse('main:fundraise_document', args=[obj.pk]))

    download_link.short_description = 'Файл'

    def has_add_permission(self, request):
        return False


# ==================== ЖУРНАЛ ДОСТУПА К ПДн ====================

@admin.register(PersonalDataAccessLog)
class PersonalDataAccessLogAdmin(admin.ModelAdmin):
    """
    Журнал обращений сотрудников к персональным данным.

    Только чтение, во всех смыслах: журнал, который сотрудник может
    отредактировать или очистить, не является доказательством и не годится
    ни для расследования утечки, ни для ответа субъекту по ст. 14 152-ФЗ.
    Удаление выполняет команда enforce_retention по истечении срока хранения.
    """

    list_display = ['created_at', 'actor_name', 'subject_name', 'data_type', 'reason', 'ip_address']
    list_filter = ['data_type', 'created_at']
    search_fields = ['actor__username', 'subject__username', 'object_repr', 'reason']
    date_hierarchy = 'created_at'
    list_per_page = 50
    readonly_fields = ['actor', 'subject', 'data_type', 'reason', 'object_repr',
                       'ip_address', 'user_agent', 'created_at']

    def actor_name(self, obj):
        return obj.actor.username if obj.actor else '—'

    actor_name.short_description = 'Кто'

    def subject_name(self, obj):
        return obj.subject.username if obj.subject else '—'

    subject_name.short_description = 'Чьи данные'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ==================== ОГРАНИЧЕНИЯ ПО УЧЁТНЫМ ЗАПИСЯМ ====================

@admin.register(AccountRestriction)
class AccountRestrictionAdmin(admin.ModelAdmin):
    """
    Применение и снятие ограничений (раздел 9 оферты).

    Сохранение новой записи отправляет пользователю уведомление с причиной:
    п. 9.2 даёт на это 1 рабочий день, и уведомление, которое надо не забыть
    отправить вручную, рано или поздно не отправляется.
    """

    list_display = ['created_at', 'user_link', 'kind', 'ground', 'notified_badge',
                    'appeal_badge', 'active_badge']
    list_filter = ['kind', 'ground', 'created_at']
    search_fields = ['user__username', 'reason', 'internal_note']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_by', 'created_at', 'notify_deadline', 'notified_at',
                       'appeal_text', 'appeal_submitted_at', 'appeal_deadline',
                       'decision', 'decision_comment', 'decided_at', 'decided_by',
                       'lifted_at', 'lifted_by']
    actions = ['lift_restrictions', 'resend_notification']

    def user_link(self, obj):
        return format_html('<a href="/admin/auth/user/{}/change/">{}</a>',
                           obj.user_id, obj.user.username)

    user_link.short_description = 'Пользователь'

    def notified_badge(self, obj):
        if obj.notified_at:
            return format_html('<span style="color: #28a745;">уведомлён</span>')
        if obj.notification_overdue:
            return format_html('<span style="color: #dc3545;">срок пропущен</span>')
        return format_html('<span style="color: #ffc107;">не уведомлён</span>')

    notified_badge.short_description = 'Уведомление (п. 9.2)'

    def appeal_badge(self, obj):
        if not obj.appeal_submitted_at:
            return '—'
        if obj.decision:
            return obj.get_decision_display()
        if obj.appeal_overdue:
            return format_html('<span style="color: #dc3545;">просрочено</span>')
        return format_html('<span style="color: #ffc107;">на рассмотрении</span>')

    appeal_badge.short_description = 'Возражение (п. 9.3)'

    def active_badge(self, obj):
        if obj.lifted_at:
            return format_html('<span style="color: #6c757d;">снято</span>')
        return format_html('<span style="color: #dc3545;">действует</span>')

    active_badge.short_description = 'Состояние'

    def save_model(self, request, obj, form, change):
        from main import restrictions as restrictions_service

        if change:
            super().save_model(request, obj, form, change)
            return

        # Новое ограничение создаётся через сервис: он пишет запись
        # в журнал безопасности и сразу пытается уведомить пользователя
        try:
            created = restrictions_service.apply_restriction(
                user=obj.user, kind=obj.kind, ground=obj.ground,
                reason=obj.reason, actor=request.user,
                internal_note=obj.internal_note,
            )
        except ValueError as exc:
            self.message_user(request, str(exc), level='error')
            return

        obj.pk = created.pk
        if created.notified_at:
            self.message_user(request, 'Пользователь уведомлён письмом с указанием причины.')
        else:
            self.message_user(
                request,
                'Уведомление отправить не удалось. Срок по п. 9.2 — 1 рабочий день, '
                'запись видна в очереди ограничений.',
                level='warning',
            )

    @admin.action(description='Снять ограничение')
    def lift_restrictions(self, request, queryset):
        lifted = 0
        for restriction in queryset.filter(lifted_at__isnull=True):
            restriction.lift(request.user, 'Снято администратором')
            lifted += 1
        self.message_user(request, f'Снято ограничений: {lifted}.')

    @admin.action(description='Отправить уведомление повторно')
    def resend_notification(self, request, queryset):
        from main import restrictions as restrictions_service

        sent = sum(1 for r in queryset if restrictions_service.notify(r))
        self.message_user(request, f'Отправлено уведомлений: {sent} из {queryset.count()}.')


# ==================== ИНЦИДЕНТЫ С ПЕРСОНАЛЬНЫМИ ДАННЫМИ ====================

@admin.register(DataBreachIncident)
class DataBreachIncidentAdmin(admin.ModelAdmin):
    """
    Журнал инцидентов и сроков по ч. 3.1 ст. 21 152-ФЗ (24 и 72 часа).

    Сроки считаются автоматически от момента, когда об инциденте стало
    известно. Уведомление подаётся человеком через портал РКН; здесь
    фиксируется факт и входящий номер — без него соблюдение срока
    доказать нечем.
    """

    list_display = ['detected_at', 'summary', 'severity', 'affected_count',
                    'initial_badge', 'final_badge']
    list_filter = ['severity', 'detected_at']
    search_fields = ['summary', 'description', 'data_categories']
    date_hierarchy = 'detected_at'
    readonly_fields = ['initial_notice_deadline', 'final_notice_deadline',
                       'created_by', 'created_at', 'procedure_hint']

    fieldsets = (
        ('Инцидент', {
            'fields': ('detected_at', 'severity', 'summary', 'description',
                       'data_categories', 'affected_count', 'suspected_cause'),
        }),
        ('Первичное уведомление — 24 часа', {
            'fields': ('initial_notice_deadline', 'initial_notice_sent_at',
                       'initial_notice_reference'),
        }),
        ('Уведомление о расследовании — 72 часа', {
            'fields': ('final_notice_deadline', 'final_notice_sent_at',
                       'final_notice_reference', 'investigation_result',
                       'measures_taken', 'subjects_notified_at'),
        }),
        ('Служебное', {'fields': ('created_by', 'created_at', 'procedure_hint')}),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def _deadline_badge(self, sent_at, reference, overdue, hours_left, label):
        if sent_at:
            return format_html('<span style="color: #28a745;">отправлено {}</span>',
                               reference or sent_at.strftime('%d.%m %H:%M'))
        if overdue:
            return format_html('<span style="color: #dc3545;">СРОК ПРОПУЩЕН</span>')
        return format_html('<span style="color: #ffc107;">осталось {} ч</span>', hours_left)

    def initial_badge(self, obj):
        return self._deadline_badge(obj.initial_notice_sent_at, obj.initial_notice_reference,
                                    obj.initial_overdue, obj.hours_to_initial, '24 ч')

    initial_badge.short_description = 'Первичное (24 ч)'

    def final_badge(self, obj):
        return self._deadline_badge(obj.final_notice_sent_at, obj.final_notice_reference,
                                    obj.final_overdue, obj.hours_to_final, '72 ч')

    final_badge.short_description = 'Расследование (72 ч)'

    def procedure_hint(self, obj):
        return format_html(
            'Порядок действий: <a href="{}" target="_blank">регламент реагирования</a>. '
            'Текст уведомления: <code>python manage.py breach_check --draft {}</code>',
            reverse('main:breach_procedure'), obj.pk or 'N')

    procedure_hint.short_description = 'Регламент'
