from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods, require_POST
from .models import (AccountRestriction, Balance, Transaction, EmailConfirmation, Fundraise,
                     FundraiseDocument, Donation, UserConsent, ConsentLog, WithdrawalRequest,
                     PersonalDataAccessLog)
from .forms import RegisterForm, TransferForm, FundraiseForm, DonationForm, WithdrawalForm
from django.core.cache import cache
from decimal import Decimal, InvalidOperation
from main.payments.yookassa import YooKassaProvider
from main.payments.security import TwoFactorAuthService, AntiFraudService, LoginRateLimiter
from main.payments.verification import KYCService
from main.payments.webhooks import WebhookRejected, verify_yookassa_notification
from main.models import (PaymentTransaction, TwoFactorAuth, SecurityLog,
                         UserVerification, KYCDocument)
from main import email_confirmation, moderation, services
from main import restrictions as restrictions_service
from main.services import InsufficientFunds, OperationRejected
from main.utils.encryption import mask_card_number, mask_phone
from main.utils.request_meta import get_client_ip, get_request_meta
import logging

logger = logging.getLogger('withdrawals')
security_logger = logging.getLogger('security')

# Ключ сессии, в котором висит пользователь, прошедший пароль, но не 2FA
PENDING_2FA_SESSION_KEY = 'pending_2fa_user_id'

ALLOWED_DOCUMENT_TYPES = {'image/jpeg', 'image/png', 'image/heic', 'application/pdf'}
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024


def validate_uploaded_document(upload):
    """
    Проверка загружаемого файла. Возвращает текст ошибки или None.

    Раньше принимался любой файл любого размера: можно было залить
    исполняемый файл или забить диск.
    """
    if upload.size > MAX_DOCUMENT_SIZE:
        return 'Файл больше 10 МБ'
    if upload.content_type not in ALLOWED_DOCUMENT_TYPES:
        return 'Допустимы только изображения JPEG, PNG, HEIC или файлы PDF'
    return None


def parse_birth_date(raw):
    """
    Разбор даты рождения из формы. Возвращает date или None.

    Раньше значение присваивалось полю модели как есть, и пустая строка
    роняла страницу верификации с 500.
    """
    from datetime import date, datetime

    if isinstance(raw, date):
        return raw
    text = (raw or '').strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def years_since(birth_date):
    """Полных лет на сегодня."""
    today = timezone.localdate()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def parse_amount(raw, field_name='Сумма'):
    """
    Разбор денежной суммы из формы.

    Раньше было Decimal(request.POST.get('amount', '0')) — любая нечисловая
    строка роняла вью с 500 (decimal.InvalidOperation).
    """
    try:
        amount = Decimal(str(raw).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        raise OperationRejected(f'{field_name} указана некорректно')
    if not amount.is_finite():
        raise OperationRejected(f'{field_name} указана некорректно')
    return services.quantize(amount)

# ==================== АУТЕНТИФИКАЦИЯ ====================

def record_consents(request, user, consent_types, version=None):
    """
    Фиксация согласий пользователя с IP и User-Agent.

    Вынесено из вью, потому что регистрация через API их не собирала вовсе:
    пользователь заводился без единой записи UserConsent.
    """
    version = version or UserConsent.current_version()
    ip_address, user_agent = get_request_meta(request)

    for consent_type in consent_types:
        UserConsent.objects.update_or_create(
            user=user,
            consent_type=consent_type,
            version=version,
            defaults={
                'is_accepted': True,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'revoked_at': None,
                'revocation_reason': None,
            },
        )
        ConsentLog.objects.create(
            user=user,
            action='accept',
            consent_type=consent_type,
            version=version,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def register_page(request):
    """Регистрация нового пользователя с сохранением согласий"""
    if request.user.is_authenticated:
        return redirect('main:dashboard')
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)

        # Согласия разнесены по основаниям. Раньше три чекбокса в одной
        # форме смешивали акцепт оферты с согласием на обработку ПДн, тогда
        # как ч. 1 ст. 9 152-ФЗ с 01.09.2025 требует оформлять согласие
        # отдельно от иных документов.
        accepted_terms = request.POST.get('accept_terms')          # акцепт оферты
        consent_data = request.POST.get('consent_data_processing')  # ст. 9
        consent_dist = request.POST.get('consent_distribution')     # ст. 10.1, необязательное

        if not accepted_terms:
            messages.error(request, 'Для регистрации нужно принять Пользовательское соглашение')
            return render(request, 'main/register.html', {'form': form})

        if not consent_data:
            messages.error(
                request,
                'Без согласия на обработку персональных данных регистрация невозможна: '
                'сервис не сможет вести вашу учётную запись.',
            )
            return render(request, 'main/register.html', {'form': form})

        if form.is_valid():
            # Регистрация и запись согласий — одна транзакция: иначе при сбое
            # появлялся бы пользователь без зафиксированного согласия на
            # обработку персональных данных.
            consent_types = ['terms', 'data_processing', 'cookies']
            if consent_dist:
                consent_types.append('distribution')

            with db_transaction.atomic():
                user = form.save()
                record_consents(request, user, consent_types)

            request.session['consent_version'] = UserConsent.current_version()
            request.session['consent_signed_at'] = timezone.now().isoformat()

            login(request, user)

            # Письмо с подтверждением адреса. Раньше адрес не проверялся
            # вовсе: опечатка означала, что восстановить доступ к деньгам
            # невозможно, а чужой адрес — что письма о чужом счёте получает
            # посторонний.
            if email_confirmation.send_confirmation(user, request):
                messages.info(
                    request,
                    f'Мы отправили письмо на {user.email}. Подтвердите адрес — '
                    f'без этого недоступны вывод средств и публикация сбора.',
                )
            else:
                messages.warning(
                    request,
                    'Не удалось отправить письмо для подтверждения адреса. '
                    'Повторить отправку можно в профиле.',
                )

            messages.success(request, f'Добро пожаловать, {user.username}!')
            return redirect('main:dashboard')
        else:
            for error in form.errors.values():
                messages.error(request, error)
    else:
        form = RegisterForm()
    
    return render(request, 'main/register.html', {'form': form})


def _complete_login(request, user, remember_me):
    """Завершение входа: срок сессии, журнал, приветствие."""
    login(request, user)
    if remember_me:
        request.session.set_expiry(settings.SESSION_REMEMBER_ME_AGE)
    else:
        # Сессия закроется при закрытии браузера
        request.session.set_expiry(0)

    AntiFraudService(user).log_security_event('login', request)
    messages.success(request, f'С возвращением, {user.username}!')
    return redirect('main:dashboard')


def login_page(request):
    """Авторизация пользователя с функцией «Запомнить меня» и 2FA."""
    if request.user.is_authenticated:
        return redirect('main:dashboard')

    limiter = LoginRateLimiter(get_client_ip(request))

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        remember_me = request.POST.get('remember_me') == 'on'

        # Защита от перебора: раньше можно было слать пароли без ограничений
        blocked_for = limiter.blocked_for(username)
        if blocked_for:
            minutes = max(1, blocked_for // 60)
            AntiFraudService(None).log_security_event(
                'suspicious', request,
                details={'reason': 'login_rate_limited', 'username': username[:150]},
            )
            messages.error(
                request,
                f'Слишком много неудачных попыток входа. Повторите через {minutes} мин.',
            )
            return render(request, 'main/login.html')

        user = authenticate(request, username=username, password=password)

        if user is None:
            limiter.register_failure(username)
            AntiFraudService(None).log_security_event(
                'failed_login', request, username_attempted=username,
            )
            # Формулировка одинакова для неверного логина и неверного пароля:
            # иначе форма превращается в способ проверять существование аккаунтов
            messages.error(request, 'Неверное имя пользователя или пароль')
            return render(request, 'main/login.html')

        limiter.reset(username)

        two_factor = TwoFactorAuth.objects.filter(user=user, is_enabled=True).first()
        if two_factor:
            # Пароль принят, но сессия ещё не выдана: вход завершится
            # только после проверки одноразового кода.
            request.session[PENDING_2FA_SESSION_KEY] = user.pk
            request.session['pending_2fa_remember'] = remember_me
            request.session['pending_2fa_started'] = timezone.now().isoformat()
            return redirect('main:login_2fa')

        return _complete_login(request, user, remember_me)

    return render(request, 'main/login.html')


def login_2fa(request):
    """Второй шаг входа: одноразовый код или резервный код."""
    user_id = request.session.get(PENDING_2FA_SESSION_KEY)
    if not user_id:
        return redirect('main:login')

    started_raw = request.session.get('pending_2fa_started')
    if started_raw:
        started = timezone.datetime.fromisoformat(started_raw)
        if timezone.now() - started > timezone.timedelta(minutes=10):
            request.session.pop(PENDING_2FA_SESSION_KEY, None)
            messages.error(request, 'Время на ввод кода истекло, войдите заново')
            return redirect('main:login')

    user = get_object_or_404(User, pk=user_id)
    two_factor = get_object_or_404(TwoFactorAuth, user=user, is_enabled=True)
    limiter = LoginRateLimiter(get_client_ip(request))

    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip()

        if limiter.blocked_for(f'2fa:{user.pk}'):
            messages.error(request, 'Слишком много попыток. Повторите позже.')
            return render(request, 'main/login_2fa.html')

        if TwoFactorAuthService.verify_code(two_factor.secret_key, code):
            two_factor.last_used = timezone.now()
            two_factor.save(update_fields=['last_used'])
        elif TwoFactorAuthService.consume_backup_code(two_factor, code):
            messages.warning(request, 'Использован резервный код — он больше не действует')
        else:
            limiter.register_failure(f'2fa:{user.pk}')
            AntiFraudService(user).log_security_event(
                'failed_login', request, details={'stage': '2fa'},
            )
            messages.error(request, 'Неверный код подтверждения')
            return render(request, 'main/login_2fa.html')

        remember_me = request.session.get('pending_2fa_remember', False)
        request.session.pop(PENDING_2FA_SESSION_KEY, None)
        request.session.pop('pending_2fa_remember', None)
        request.session.pop('pending_2fa_started', None)
        limiter.reset(f'2fa:{user.pk}')
        return _complete_login(request, user, remember_me)

    return render(request, 'main/login_2fa.html')


@login_required
def logout_page(request):
    """Выход из системы"""
    AntiFraudService(request.user).log_security_event('logout', request)
    logout(request)
    messages.info(request, 'Вы вышли из системы')
    return redirect('main:login')


# ==================== ОСНОВНЫЕ СТРАНИЦЫ ====================

@login_required
def dashboard(request):
    """Главная страница с балансом и последними транзакциями"""
    balance = request.user.balance.amount
    
    recent_transactions = Transaction.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).select_related('sender', 'receiver').order_by('-created_at')[:10]
    
    total_sent = Transaction.objects.filter(
        sender=request.user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    total_received = Transaction.objects.filter(
        receiver=request.user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    context = {
        'balance': balance,
        'recent_transactions': recent_transactions,
        'total_sent': total_sent,
        'total_received': total_received,
    }
    return render(request, 'main/dashboard.html', context)


@login_required
def transfer_money(request):
    """Страница перевода денег"""
    if request.method == 'POST':
        form = TransferForm(request.POST)
        if form.is_valid():
            receiver_username = form.cleaned_data['receiver_username']
            amount = form.cleaned_data['amount']
            comment = form.cleaned_data.get('comment', '')
            
            try:
                # iexact: раньше поиск был чувствителен к регистру, и «Ivan»
                # не находился, хотя подсказка в форме показывала именно его
                receiver = User.objects.get(username__iexact=receiver_username)
            except User.DoesNotExist:
                messages.error(request, f'Пользователь «{receiver_username}» не найден')
                return redirect('main:transfer')

            try:
                services.transfer(request.user, receiver, amount, comment)
            except (InsufficientFunds, OperationRejected) as exc:
                messages.error(request, str(exc))
                return redirect('main:transfer')
            except Exception:
                # Внутренние детали не показываем пользователю — раньше
                # str(e) утекал прямо на страницу
                logger.exception('Ошибка перевода от %s', request.user.username)
                messages.error(request, 'Не удалось выполнить перевод, попробуйте позже')
                return redirect('main:transfer')

            messages.success(
                request,
                f'✅ Перевод {amount} ₽ пользователю {receiver.username} выполнен!',
            )
            return redirect('main:dashboard')
    else:
        form = TransferForm()
    
    recent_receivers = Transaction.objects.filter(
        sender=request.user, status='completed'
    ).values_list('receiver__username', flat=True).distinct()[:5]
    
    context = {
        'form': form,
        'recent_receivers': recent_receivers,
        'current_balance': request.user.balance.amount,
    }
    return render(request, 'main/transfer.html', context)


@login_required
def transaction_history(request):
    """История всех транзакций"""
    transactions = Transaction.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user)
    ).select_related('sender', 'receiver').order_by('-created_at')
    
    filter_type = request.GET.get('filter', 'all')
    if filter_type == 'sent':
        transactions = transactions.filter(sender=request.user)
    elif filter_type == 'received':
        transactions = transactions.filter(receiver=request.user)
    
    search_query = request.GET.get('search', '')
    if search_query:
        transactions = transactions.filter(
            Q(sender__username__icontains=search_query) |
            Q(receiver__username__icontains=search_query)
        )
    
    paginator = Paginator(transactions, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'filter_type': filter_type,
        'search_query': search_query,
    }
    return render(request, 'main/history.html', context)


@login_required
def top_up_balance(request):
    """
    Пополнение баланса.

    Реальные платежи идут через create_payment. Симуляция оставлена только
    для локальной разработки и ограничена суммарным потолком: раньше два
    POST-запроса подряд давали 200 000 ₽ из ничего, а при ошибочной
    переменной окружения это была бы неограниченная эмиссия денег.
    """
    if request.method == 'POST':
        if not settings.ALLOW_SIMULATED_TOPUP:
            # Боевой режим: пополнение только через платёжную систему
            return create_payment(request)

        try:
            amount = parse_amount(request.POST.get('amount'), 'Сумма пополнения')
        except OperationRejected as exc:
            messages.error(request, str(exc))
            return redirect('main:topup')

        if amount < settings.MIN_TOPUP_AMOUNT:
            messages.error(request, f'Минимальная сумма пополнения — {settings.MIN_TOPUP_AMOUNT} ₽')
            return redirect('main:topup')

        if amount > settings.MAX_TOPUP_AMOUNT:
            messages.error(request, f'Максимальная сумма пополнения — {settings.MAX_TOPUP_AMOUNT} ₽')
            return redirect('main:topup')

        simulated_total = Transaction.objects.filter(
            sender=request.user, kind='topup', status='completed',
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

        if simulated_total + amount > settings.SIMULATED_TOPUP_TOTAL_LIMIT:
            messages.error(
                request,
                f'В тестовом режиме суммарное пополнение ограничено '
                f'{settings.SIMULATED_TOPUP_TOTAL_LIMIT} ₽ (уже пополнено {simulated_total} ₽)',
            )
            return redirect('main:topup')

        with db_transaction.atomic():
            balance = Balance.objects.select_for_update().get(user=request.user)
            balance.amount += amount
            balance.save(update_fields=['amount'])

            Transaction.objects.create(
                sender=request.user,
                receiver=request.user,
                amount=amount,
                comment='Пополнение баланса (тестовый режим)',
                status='completed',
                kind='topup',
            )

        messages.success(request, f'💰 Баланс пополнен на {amount} ₽ (тестовый режим)')
        return redirect('main:dashboard')

    return render(request, 'main/topup.html', {
        'simulated': settings.ALLOW_SIMULATED_TOPUP,
        'min_amount': settings.MIN_TOPUP_AMOUNT,
        'max_amount': settings.MAX_TOPUP_AMOUNT,
    })


@login_required
def user_profile(request, username=None):
    """Профиль пользователя"""
    if username:
        profile_user = get_object_or_404(User, username=username)
        is_own_profile = (profile_user == request.user)
    else:
        profile_user = request.user
        is_own_profile = True
    
    # kind='topup' исключается из оборотов: пополнение записывается как
    # перевод самому себе и раньше попадало одновременно в «отправлено»
    # и «получено», задваивая суммы.
    turnover = Transaction.objects.filter(status='completed').exclude(kind__in=['topup', 'refund'])

    total_sent = turnover.filter(sender=profile_user).aggregate(Sum('amount'))['amount__sum'] or 0
    total_received = turnover.filter(receiver=profile_user).aggregate(Sum('amount'))['amount__sum'] or 0
    transactions_count = turnover.filter(
        Q(sender=profile_user) | Q(receiver=profile_user)
    ).count()

    context = {
        'profile_user': profile_user,
        'is_own_profile': is_own_profile,
        'total_sent': total_sent,
        'total_received': total_received,
        'transactions_count': transactions_count,
        'member_since': profile_user.date_joined,
    }

    # Баланс и история операций — только в собственном профиле.
    # Раньше страница /profile/<username>/ показывала чужой остаток
    # и чужие транзакции любому авторизованному пользователю.
    if is_own_profile:
        context['balance'] = profile_user.balance.amount
        context['recent_transactions'] = Transaction.objects.filter(
            Q(sender=profile_user) | Q(receiver=profile_user)
        ).select_related('sender', 'receiver').order_by('-created_at')[:5]
        confirmation = EmailConfirmation.for_user(profile_user)
        context['email_confirmed'] = confirmation.is_confirmed
        context['email_confirmation'] = confirmation
    else:
        context['recent_transactions'] = []

    return render(request, 'main/profile.html', context)


@login_required
def leaders_board(request):
    """
    Рейтинг пользователей.

    Пополнения исключены из оборота (иначе достаточно было пополнить и
    вывести, чтобы возглавить оба списка), балансы не показываются вовсе.
    """
    turnover = Q(sent_transactions__status='completed') & ~Q(sent_transactions__kind__in=['topup', 'refund'])

    top_senders = User.objects.annotate(
        total_sent=Sum('sent_transactions__amount', filter=turnover)
    ).filter(total_sent__gt=0).order_by('-total_sent')[:10]

    received = Q(received_transactions__status='completed') & ~Q(
        received_transactions__kind__in=['topup', 'refund']
    )
    top_receivers = User.objects.annotate(
        total_received=Sum('received_transactions__amount', filter=received)
    ).filter(total_received__gt=0).order_by('-total_received')[:10]

    context = {
        'top_senders': top_senders,
        'top_receivers': top_receivers,
    }
    return render(request, 'main/leaders.html', context)


@login_required
def quick_help(request):
    """Быстрая помощь: предложить случайного пользователя для перевода."""
    import random

    # order_by('?')[:1] вместо random.choice(list(...)): прежний вариант
    # загружал в память всех подходящих пользователей целиком.
    suggested_user = User.objects.exclude(id=request.user.id).filter(
        received_transactions__isnull=False
    ).distinct().order_by('?').first()

    if suggested_user is None:
        messages.info(request, 'Пока нет активных пользователей для помощи')
        return redirect('main:dashboard')

    context = {
        'suggested_user': suggested_user,
        'suggested_amount': 50,
        'current_balance': request.user.balance.amount,
    }
    return render(request, 'main/quick_help.html', context)


# ==================== API ENDPOINTS ====================

@login_required
def check_username(request):
    """
    Проверка существования пользователя перед переводом.

    iexact вместо icontains: прежняя проверка на запрос «z» отвечала
    exists=true, если существовал хоть кто-то с «z» в имени, — то есть
    сообщала не то, что спрашивали, и заодно работала как оракул для
    перебора имён по подстроке.
    """
    username = (request.GET.get('username') or '').strip()
    if not username:
        return JsonResponse({'exists': False})

    exists = User.objects.filter(username__iexact=username).exists()
    return JsonResponse({'exists': exists, 'username': username})


# Удалены get_balance_json и cancel_transaction.
#
# Первую не вызывал ни один шаблон и ни один скрипт: баланс и так есть
# на каждой странице. Вторая отменяла транзакцию со статусом 'pending',
# а таких транзакций в системе не создаётся нигде — все операции
# завершаются в момент выполнения. То есть вью существовала, была
# доступна по адресу, но сработать не могла ни при каких данных.


# ==================== СБОРЫ СРЕДСТВ ====================

@login_required
def fundraise_list(request):
    """
    Страница со списком сборов.

    Выборка идёт через Fundraise.published(): раньше здесь было
    Fundraise.objects.all(), и непроверенный сбор — в том числе созданный
    минуту назад и ещё никем не прочитанный — сразу попадал на витрину.
    """
    category = request.GET.get('category', 'all')
    status = request.GET.get('status', 'active')

    fundraises = Fundraise.published()

    if category != 'all':
        fundraises = fundraises.filter(category=category)
    if status != 'all':
        fundraises = fundraises.filter(status=status)
    
    # Статистика
    total_fundraises = fundraises.count()
    total_raised = fundraises.aggregate(Sum('current_amount'))['current_amount__sum'] or 0
    # Считаются жертвователи показанных сборов, а не все доноры платформы:
    # раньше цифра под фильтром «Медицина» включала вообще всех.
    total_donors = (
        Donation.objects.filter(fundraise__in=fundraises, refunded_at__isnull=True)
        .values('donor').distinct().count()
    )

    paginator = Paginator(fundraises.select_related('author'), 12)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'fundraises': page.object_list,
        'page_obj': page,
        'total_fundraises': total_fundraises,
        'total_raised': total_raised,
        'total_donors': total_donors,
        'selected_category': category,
        'selected_status': status,
    }
    return render(request, 'main/fundraises.html', context)


@login_required
def fundraise_detail(request, pk):
    """
    Детальная страница сбора.

    Непроверенный сбор виден только автору и модератору. Иначе проверка
    обходится прямой ссылкой: сбор не в списке, но страница открыта всем,
    и ссылку достаточно разослать самому.
    """
    fundraise = get_object_or_404(Fundraise, pk=pk)

    # Проверка, является ли пользователь автором сбора
    is_author = (request.user == fundraise.author)
    can_moderate = request.user.is_staff

    # Жертвователь видит сбор всегда: он отдал в него деньги, и эта страница —
    # единственное место, где видна их судьба. Скрыть её от него значило бы
    # лишить его сведений о собственной операции.
    is_donor = fundraise.donations.filter(donor=request.user).exists()

    if not fundraise.is_public and not (is_author or can_moderate or is_donor):
        raise Http404('Сбор не найден')

    donations = fundraise.donations.all()[:20]

    # Последние пожертвования
    recent_donations = fundraise.donations.select_related('donor').order_by('-created_at')[:10]

    if request.method == 'POST':
        # Запрещаем автору жертвовать самому себе
        if is_author:
            messages.error(request, '❌ Вы не можете пожертвовать средства в свой собственный сбор')
            return redirect('main:fundraise_detail', pk=pk)

        if not fundraise.accepts_donations:
            messages.error(request, 'Сбор не принимает пожертвования')
            return redirect('main:fundraise_detail', pk=pk)

        form = DonationForm(request.POST)
        if form.is_valid():
            try:
                services.donate(
                    donor=request.user,
                    fundraise=fundraise,
                    amount=form.cleaned_data['amount'],
                    message=form.cleaned_data.get('message', ''),
                    is_anonymous=form.cleaned_data.get('is_anonymous', False),
                )
            except (InsufficientFunds, OperationRejected) as exc:
                messages.error(request, str(exc))
                return redirect('main:fundraise_detail', pk=pk)
            except Exception:
                logger.exception('Ошибка пожертвования в сбор #%s', pk)
                messages.error(request, 'Не удалось выполнить пожертвование, попробуйте позже')
                return redirect('main:fundraise_detail', pk=pk)

            messages.success(
                request,
                f'✅ Спасибо за пожертвование! '
                f'{form.cleaned_data["amount"]} ₽ отправлено в сбор «{fundraise.title}».',
            )
            return redirect('main:fundraise_detail', pk=pk)
        else:
            messages.error(request, 'Пожалуйста, исправьте ошибки в форме')
    else:
        form = DonationForm()

    context = {
        'fundraise': fundraise,
        'donations': donations,
        'recent_donations': recent_donations,
        'form': form,
        'progress_percent': fundraise.get_progress_percent(),
        'is_author': is_author,
        'can_moderate': can_moderate,
        'accepts_donations': fundraise.accepts_donations,
        # Сколько из собранного фактически зачислено автору: раньше в шаблоне
        # стояло «Вы получите: {{ current_amount }}» без учёта комиссии
        'author_received': (
            fundraise.current_amount
            - sum(
                (donation.commission.amount for donation in fundraise.donations.select_related('commission')
                 if getattr(donation, 'commission', None)),
                Decimal('0.00'),
            )
        ),
        # Ставка берётся из настроек — единственного места, где она задана
        'commission_percent': settings.DONATION_COMMISSION_PERCENT,
    }
    return render(request, 'main/fundraise_detail.html', context)


@login_required
def create_fundraise(request):
    """
    Создание сбора средств.

    Публикация сбора делает имя автора и описание доступными неограниченному
    кругу лиц, поэтому требует согласия по ст. 10.1 152-ФЗ. Раньше согласие
    описывалось в документах, но нигде не проверялось — сбор создавался
    и без него.
    """
    has_consent = UserConsent.has_active_consent(
        request.user, 'distribution', UserConsent.current_version(),
    )
    if not has_consent:
        messages.error(
            request,
            'Для публикации сбора нужно согласие на распространение персональных '
            'данных: имя автора и описание сбора становятся общедоступными. '
            'Дать согласие можно в разделе «Мои согласия».',
        )
        return redirect('main:my_consents')

    # Незавершённым считается и черновик, и сбор на проверке: иначе можно
    # держать десяток заготовок и отправлять их на модерацию пачкой.
    has_unfinished = Fundraise.objects.filter(
        author=request.user, status__in=Fundraise.UNFINISHED_STATUSES,
    ).exists()

    if has_unfinished:
        messages.error(
            request,
            'У вас уже есть незавершённый сбор. Завершите или отмените его, '
            'прежде чем создавать новый.',
        )
        return redirect('main:my_fundraises')

    if request.method == 'POST':
        form = FundraiseForm(request.POST)
        if form.is_valid():
            fundraise = form.save(commit=False)
            fundraise.author = request.user
            # Сбор создаётся черновиком и публикуется только после проверки
            fundraise.status = 'draft'
            fundraise.moderation_status = 'draft'
            fundraise.save()
            messages.success(
                request,
                f'Черновик сбора «{fundraise.title}» создан. '
                f'Проверьте данные и отправьте его на модерацию.',
            )
            return redirect('main:fundraise_manage', pk=fundraise.pk)
    else:
        form = FundraiseForm()

    return render(request, 'main/create_fundraise.html', {'form': form})


def _author_fundraise(request, pk):
    """Сбор, которым распоряжается текущий пользователь."""
    return get_object_or_404(Fundraise, pk=pk, author=request.user)


@login_required
def fundraise_manage(request, pk):
    """
    Страница подготовки сбора: правка, документы, отправка на проверку.

    Автор видит здесь ровно то, что мешает публикации, — список из
    moderation.check_can_submit, а не общую фразу «заявка отклонена».
    """
    fundraise = _author_fundraise(request, pk)

    if request.method == 'POST':
        if not fundraise.is_editable:
            messages.error(request, 'Сбор на проверке — дождитесь решения модератора.')
            return redirect('main:fundraise_manage', pk=pk)
        form = FundraiseForm(request.POST, instance=fundraise)
        if form.is_valid():
            changed = set(form.changed_data) & set(Fundraise.MODERATED_FIELDS)
            fundraise = form.save()
            # Правка проверенного текста возвращает сбор на модерацию:
            # иначе одобрение получают на безобидном описании, а потом
            # подменяют его — проверка становится формальностью.
            if changed and fundraise.moderation_status == 'approved':
                fundraise.reset_moderation()
                messages.warning(
                    request,
                    'Изменены проверенные сведения, сбор снят с публикации '
                    'и требует повторной проверки.',
                )
            else:
                messages.success(request, 'Изменения сохранены.')
            return redirect('main:fundraise_manage', pk=pk)
    else:
        form = FundraiseForm(instance=fundraise)

    problems = moderation.check_can_submit(fundraise)
    context = {
        'fundraise': fundraise,
        'form': form,
        'problems': problems,
        'documents': fundraise.documents.all(),
        'document_types': FundraiseDocument.DOCUMENT_TYPES,
        'requires_documents': fundraise.category in moderation.CATEGORIES_REQUIRING_DOCUMENTS,
        'can_submit': not problems and fundraise.is_editable,
    }
    return render(request, 'main/fundraise_manage.html', context)


@login_required
@require_POST
def submit_fundraise(request, pk):
    """Отправка сбора на проверку."""
    fundraise = _author_fundraise(request, pk)

    if not fundraise.is_editable:
        messages.error(request, 'Сбор уже на проверке.')
        return redirect('main:fundraise_manage', pk=pk)

    problems = moderation.check_can_submit(fundraise)
    if problems:
        for problem in problems:
            messages.error(request, problem)
        return redirect('main:fundraise_manage', pk=pk)

    fundraise.submit_for_moderation()
    messages.success(
        request,
        'Сбор отправлен на проверку. Обычно она занимает до одного рабочего дня — '
        'решение придёт в раздел «Мои сборы».',
    )
    return redirect('main:my_fundraises')


@login_required
@require_POST
def upload_fundraise_document(request, pk):
    """
    Загрузка документа, подтверждающего цель сбора.

    Файл идёт в приватное хранилище: справка о диагнозе — специальная
    категория ПДн (ст. 10 152-ФЗ), и раздача её по прямой ссылке из
    /media/ была бы нарушением сама по себе.
    """
    fundraise = _author_fundraise(request, pk)

    if not fundraise.is_editable:
        messages.error(request, 'Сбор на проверке — приложить документ уже нельзя.')
        return redirect('main:fundraise_manage', pk=pk)

    upload = request.FILES.get('file')
    document_type = request.POST.get('document_type', '')

    if not upload:
        messages.error(request, 'Выберите файл.')
        return redirect('main:fundraise_manage', pk=pk)
    if document_type not in dict(FundraiseDocument.DOCUMENT_TYPES):
        messages.error(request, 'Выберите тип документа.')
        return redirect('main:fundraise_manage', pk=pk)

    error = validate_uploaded_document(upload)
    if error:
        messages.error(request, error)
        return redirect('main:fundraise_manage', pk=pk)

    if fundraise.documents.count() >= settings.FUNDRAISE_MAX_DOCUMENTS:
        messages.error(
            request,
            f'К сбору можно приложить не более '
            f'{settings.FUNDRAISE_MAX_DOCUMENTS} документов.',
        )
        return redirect('main:fundraise_manage', pk=pk)

    FundraiseDocument.objects.create(
        fundraise=fundraise,
        document_type=document_type,
        file=upload,
        comment=(request.POST.get('comment') or '')[:1000],
    )
    messages.success(request, 'Документ приложен. Его увидит только модератор.')
    return redirect('main:fundraise_manage', pk=pk)


@login_required
@require_POST
def delete_fundraise_document(request, pk):
    """
    Удаление приложенного документа автором.

    Загрузка была, удаления — нет: ошибочно приложенную чужую справку
    нельзя было убрать. Для документа, который может содержать сведения
    о здоровье, это прямое нарушение права на уничтожение данных
    (ст. 14 152-ФЗ).
    """
    document = get_object_or_404(
        FundraiseDocument.objects.select_related('fundraise'),
        pk=pk, fundraise__author=request.user,
    )
    fundraise_id = document.fundraise_id

    if not document.fundraise.is_editable:
        messages.error(request, 'Сбор на проверке — состав документов менять уже нельзя.')
        return redirect('main:fundraise_manage', pk=fundraise_id)

    document.file.delete(save=False)
    document.delete()
    messages.success(request, 'Документ удалён.')
    return redirect('main:fundraise_manage', pk=fundraise_id)


@login_required
def fundraise_document(request, pk):
    """
    Выдача приложенного документа.

    Доступ только автору и модератору, и каждое открытие модератором
    записывается в журнал: это просмотр сведений о здоровье, а Django
    сам по себе фиксирует только изменения, но не чтение.
    """
    document = get_object_or_404(FundraiseDocument.objects.select_related('fundraise'), pk=pk)
    is_author = document.fundraise.author_id == request.user.pk

    if not (is_author or request.user.is_staff):
        raise Http404('Документ не найден')

    if not is_author:
        PersonalDataAccessLog.record(
            actor=request.user,
            subject=document.fundraise.author,
            data_type='fundraise_document',
            reason='Проверка сбора средств',
            request=request,
            object_repr=f'FundraiseDocument #{document.pk} (сбор #{document.fundraise_id})',
        )

    try:
        handle = document.file.open('rb')
    except FileNotFoundError:
        raise Http404('Файл недоступен')
    return FileResponse(handle, as_attachment=True, filename=document.display_name)


@login_required
def my_fundraises(request):
    """Мои сборы"""
    own = Fundraise.objects.filter(author=request.user)
    # Черновики и заявки на проверке показываются отдельно: раньше сбор,
    # не попавший ни в один из трёх статусов, просто пропадал из списка.
    draft_fundraises = own.filter(status='draft')
    active_fundraises = own.filter(status='active')
    completed_fundraises = own.filter(status='completed')
    cancelled_fundraises = own.filter(status='cancelled')

    has_unfinished = own.filter(status__in=Fundraise.UNFINISHED_STATUSES).exists()

    context = {
        'draft_fundraises': draft_fundraises,
        # Долг показывается там, где автор и так бывает: страницу, о которой
        # не знаешь, не открывают
        'debt_total': services.outstanding_debt(request.user)['total'],
        'active_fundraises': active_fundraises,
        'completed_fundraises': completed_fundraises,
        'cancelled_fundraises': cancelled_fundraises,
        'has_active_fundraise': has_unfinished,
        'has_unfinished': has_unfinished,
    }
    return render(request, 'main/my_fundraises.html', context)


@login_required
def my_debt(request):
    """
    Долг перед жертвователями и его погашение.

    Пункт 7.4 оферты обещает, что невозвращённая часть фиксируется как
    задолженность и погашается. Фиксировалась она исправно, а вот увидеть
    её и вернуть деньги автор не мог никак: ни страницы, ни кнопки.
    """
    debt = services.outstanding_debt(request.user)

    if request.method == 'POST':
        if debt['total'] <= 0:
            messages.info(request, 'Задолженности нет.')
            return redirect('main:my_debt')

        balance = Balance.objects.get(user=request.user).amount
        if balance <= 0:
            messages.error(
                request,
                'На балансе нет средств. Пополните баланс — деньги уйдут '
                'жертвователям сразу после пополнения.',
            )
            return redirect('main:my_debt')

        try:
            repaid = services.repay_debt(request.user)
        except (InsufficientFunds, OperationRejected) as exc:
            messages.error(request, str(exc))
            return redirect('main:my_debt')

        remaining = services.outstanding_debt(request.user)['total']
        if repaid > 0:
            text = f'Жертвователям возвращено {repaid} ₽.'
            if remaining > 0:
                text += f' Осталось вернуть {remaining} ₽.'
            messages.success(request, text)
        else:
            messages.error(request, 'Вернуть не удалось: проверьте остаток на балансе.')
        return redirect('main:my_debt')

    return render(request, 'main/my_debt.html', {
        'debt': debt,
        'balance': Balance.objects.get(user=request.user).amount,
    })


@login_required
def my_donations(request):
    """Мои пожертвования"""
    donations = Donation.objects.filter(donor=request.user).select_related('fundraise').order_by('-created_at')

    # Шаблон рисует постраничную навигацию по page_obj, которого вью
    # не передавала: блок не отображался, а все пожертвования шли одним
    # списком без конца.
    paginator = Paginator(donations, 20)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'donations': page.object_list,
        'page_obj': page,
        'total_donated': donations.aggregate(Sum('amount'))['amount__sum'] or 0,
    }
    return render(request, 'main/my_donations.html', context)


@login_required
def my_consents(request):
    """Страница управления согласиями пользователя"""
    consents = UserConsent.objects.filter(user=request.user, is_accepted=True, revoked_at__isnull=True)
    consent_logs = ConsentLog.objects.filter(user=request.user)[:20]
    
    if request.method == 'POST':
        action = request.POST.get('action')
        consent_type = request.POST.get('consent_type')

        # Тип берётся из POST и раньше не проверялся: можно было записать
        # согласие с произвольными типом и версией, отравив доказательственную
        # базу («ВЗЛОМ», версия «9.9»).
        valid_types = {choice[0] for choice in UserConsent.CONSENT_TYPES}
        if consent_type not in valid_types:
            messages.error(request, 'Неизвестный тип согласия')
            return redirect('main:my_consents')

        if action == 'revoke':
            consent = UserConsent.objects.filter(
                user=request.user,
                consent_type=consent_type,
                is_accepted=True,
                revoked_at__isnull=True,
            ).first()

            if not consent:
                messages.error(request, 'Активное согласие не найдено')
                return redirect('main:my_consents')

            # Отзыв согласия, без которого услуга невозможна, равносилен
            # расторжению договора — так и написано в документах. Молча
            # оставлять аккаунт работающим нельзя, но и удалять его без
            # явного решения пользователя тоже.
            if consent_type in UserConsent.REQUIRED_TYPES:
                messages.error(
                    request,
                    'Без этого согласия сервис не может вести вашу учётную запись. '
                    'Его отзыв означает прекращение договора — используйте '
                    'удаление учётной записи ниже: оно уничтожит данные и '
                    'потребует сначала вывести остаток средств.',
                )
                return redirect('main:my_consents')

            reason = (request.POST.get('reason') or '')[:500]
            ip_address, user_agent = get_request_meta(request)

            with db_transaction.atomic():
                consent.revoke(reason)
                ConsentLog.objects.create(
                    user=request.user,
                    action='revoke',
                    consent_type=consent_type,
                    version=consent.version,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

                # Отзыв согласия на распространение обязан снять публикации:
                # документ обещает, что сбор снимается со страниц сервиса
                if consent_type == 'distribution':
                    active = Fundraise.objects.filter(author=request.user, status='active')
                    for fundraise in active:
                        result = services.refund_donations(
                            fundraise, reason='отозвано согласие на распространение',
                        )
                        fundraise.status = 'cancelled'
                        fundraise.save(update_fields=['status'])
                        if result['refunded']:
                            messages.info(
                                request,
                                f'Сбор «{fundraise.title}» снят с публикации, '
                                f'жертвователям возвращено {result["refunded"]} ₽.',
                            )

            messages.info(request, f'Согласие на «{consent.get_consent_type_display()}» отозвано')

        elif action == 'reaccept':
            # update_or_create вместо create: при живом unique_together
            # повторное принятие отозванного согласия падало с IntegrityError
            record_consents(request, request.user, [consent_type])
            messages.success(
                request,
                f'Согласие на «{dict(UserConsent.CONSENT_TYPES).get(consent_type)}» принято',
            )

        return redirect('main:my_consents')
    
    context = {
        'consents': consents,
        'consent_logs': consent_logs,
        'available_consents': _available_consents(request.user),
        'current_version': UserConsent.current_version(),
    }
    return render(request, 'main/my_consents.html', context)


# Документ, который пользователь подписывает, принимая согласие. Без ссылки
# на текст «принятие» ничего не значит: согласие должно быть информированным
# (ч. 1 ст. 9 152-ФЗ).
CONSENT_DOCUMENTS = {
    'terms': ('main:user_agreement', 'Пользовательское соглашение'),
    'data_processing': ('main:consent_processing', 'Согласие на обработку персональных данных'),
    'distribution': ('main:consent_distribution', 'Согласие на распространение персональных данных'),
    'cookies': ('main:cookie_policy', 'Политика cookies'),
}


def _available_consents(user):
    """
    Согласия, которые пользователь может дать прямо сейчас.

    Раньше этого списка не было, и принять согласие было негде: вью умела
    обрабатывать action=reaccept, но ни одной кнопки в интерфейсе не
    существовало. Пользователь без согласия на распространение не мог
    создать сбор — и не мог его выдать. То же после обновления редакции
    документов: старое согласие перестаёт действовать, а переподписать
    его было нечем.
    """
    version = UserConsent.current_version()
    active = set(
        UserConsent.objects.filter(
            user=user, is_accepted=True, revoked_at__isnull=True, version=version,
        ).values_list('consent_type', flat=True)
    )

    available = []
    for consent_type, (url_name, title) in CONSENT_DOCUMENTS.items():
        if consent_type in active:
            continue
        available.append({
            'type': consent_type,
            'title': title,
            'url_name': url_name,
            'required': consent_type in UserConsent.REQUIRED_TYPES,
            'outdated': UserConsent.objects.filter(
                user=user, consent_type=consent_type, is_accepted=True,
                revoked_at__isnull=True,
            ).exclude(version=version).exists(),
        })
    return available


@login_required
@require_POST
def complete_fundraise(request, pk):
    """
    Завершение сбора автором.

    Только POST: завершение необратимо, а вызываемое переходом по ссылке
    необратимое действие срабатывает от картинки на стороннем сайте.
    Ровно по этой причине POST требуется и для отмены сбора.
    """
    fundraise = get_object_or_404(Fundraise, pk=pk, author=request.user)

    # Завершить можно только проверенный сбор. Иначе так закрывался сбор,
    # снятый с публикации после правки: завершённый сбор модерации уже
    # не подлежит, и отклонить его с возвратом денег было бы нельзя.
    if fundraise.moderation_status != 'approved':
        messages.error(
            request,
            'Сбор не прошёл проверку — завершить его нельзя. '
            'Отмените его, если он больше не нужен: жертвователи получат деньги обратно.',
        )
        return redirect('main:my_fundraises')

    if fundraise.status == 'active':
        fundraise.status = 'completed'
        fundraise.save()
        
        # Было: f'...{fundraise.current_amount | floatformat:2}...' — внутрь
        # f-строки Python попал фильтр шаблонизатора Django, и завершение
        # сбора всегда падало с NameError: floatformat.
        messages.success(
            request,
            f'Сбор «{fundraise.title}» успешно завершён. '
            f'Собранные средства ({fundraise.current_amount:.2f} ₽) зачислены на ваш баланс.',
        )

    else:
        messages.error(request, 'Этот сбор уже завершен или отменен')
    
    return redirect('main:my_fundraises')


@login_required
def create_withdrawal_request(request):
    """
    Создание заявки на вывод средств.

    Раньше вью делала собственные слабые проверки, а два написанных
    валидатора лимитов по уровню верификации не вызывались вообще —
    неверифицированный пользователь спокойно выводил 100 000 ₽ при
    заявленном лимите 500 ₽. Теперь проверка одна и она обязательна.
    """
    from main.payments.withdrawals import WithdrawalValidator

    if request.method == 'POST':
        form = WithdrawalForm(request.POST)
        if form.is_valid():
            amount = services.quantize(form.cleaned_data['amount'])
            payment_method = form.cleaned_data['payment_method']

            if payment_method == 'card':
                payment_details = {
                    'card_number': form.cleaned_data.get('card_number'),
                    'card_holder': form.cleaned_data.get('card_holder'),
                    'expiry_date': form.cleaned_data.get('expiry_date'),
                }
                masked = mask_card_number(payment_details['card_number'])
            elif payment_method == 'sbp':
                # bank_id обязателен: без него ЮKassa не знает, в какой банк
                # переводить, и выплата не проходит
                payment_details = {
                    'phone_number': form.cleaned_data.get('phone_number'),
                    'bank_id': form.cleaned_data.get('bank_id'),
                }
                masked = mask_phone(payment_details['phone_number'])
            else:
                payment_details = {'wallet_number': form.cleaned_data.get('wallet_number')}
                wallet = str(payment_details['wallet_number'] or '')
                masked = '***' + wallet[-4:] if len(wallet) >= 4 else '***'

            is_valid, error = WithdrawalValidator.validate_withdrawal_request(
                user=request.user,
                amount=amount,
                payment_details=payment_details,
                payment_method=payment_method,
            )
            if not is_valid:
                messages.error(request, error)
                return redirect('main:withdrawal')

            risk_level, risks = AntiFraudService(request.user).check_withdrawal(
                amount, get_client_ip(request),
            )

            try:
                withdrawal = services.hold_for_withdrawal(
                    user=request.user,
                    amount=amount,
                    payment_method=payment_method,
                    payment_details=payment_details,
                    masked=masked,
                )
            except (InsufficientFunds, OperationRejected) as exc:
                messages.error(request, str(exc))
                return redirect('main:withdrawal')
            except Exception:
                logger.exception('Ошибка создания заявки на вывод')
                messages.error(request, 'Не удалось создать заявку, попробуйте позже')
                return redirect('main:withdrawal')

            logger.info(
                'Создана заявка #%s на вывод %s ₽ от %s (риск: %s)',
                withdrawal.id, amount, request.user.username, risk_level,
            )
            AntiFraudService(request.user).log_security_event(
                'withdrawal', request,
                details={
                    'withdrawal_id': withdrawal.id,
                    'amount': str(amount),
                    'risk_level': risk_level,
                    'risks': risks,
                },
            )

            messages.success(
                request,
                f'Заявка на вывод {amount} ₽ создана. Статус: {withdrawal.get_status_display()}',
            )
            return redirect('main:my_withdrawals')
    else:
        form = WithdrawalForm()

    verification = getattr(request.user, 'verification', None)
    limits = KYCService.get_verification_limits(
        verification.level if verification else 'unverified'
    )

    context = {
        'form': form,
        'current_balance': request.user.balance.amount,
        'min_amount': settings.MIN_WITHDRAWAL_AMOUNT,
        'max_amount': min(settings.MAX_WITHDRAWAL_AMOUNT, limits['single_withdrawal']),
        'limits': limits,
        'verification_level': verification.level if verification else 'unverified',
    }
    return render(request, 'main/withdrawal.html', context)


@login_required
def my_withdrawals(request):
    """Список моих заявок на вывод"""
    withdrawals = WithdrawalRequest.objects.filter(user=request.user).order_by('-created_at')
    
    context = {
        'withdrawals': withdrawals,
        'pending_count': withdrawals.filter(status='pending').count(),
    }
    return render(request, 'main/my_withdrawals.html', context)


@login_required
@require_POST
def cancel_withdrawal(request, pk):
    """
    Отмена заявки пользователем.

    Только POST: отмена возвращает деньги на баланс, а действие с деньгами,
    доступное переходом по ссылке, срабатывает от картинки на чужом сайте.
    """
    withdrawal = get_object_or_404(WithdrawalRequest, pk=pk, user=request.user)
    
    if withdrawal.cancel():
        messages.success(request, f'Заявка #{withdrawal.id} отменена, средства возвращены на баланс')
    else:
        messages.error(request, 'Невозможно отменить заявку в текущем статусе')
    
    return redirect('main:my_withdrawals')

@login_required
@require_POST
def cancel_fundraise(request, pk):
    """
    Отмена сбора автором с возвратом пожертвований.

    Раньше отмена просто меняла статус, а деньги оставались у автора:
    можно было собрать средства «на лечение», отменить сбор и вывести их.
    Теперь отмена обязательно сопровождается возвратом донорам.

    Метод только POST: отмена — необратимое действие с деньгами, её нельзя
    вызывать переходом по ссылке (в том числе чужой картинкой на стороннем
    сайте, что при GET работало бы как CSRF).
    """
    fundraise = get_object_or_404(Fundraise, pk=pk, author=request.user)

    if fundraise.status != 'active':
        messages.error(request, 'Этот сбор уже завершён или отменён')
        return redirect('main:my_fundraises')

    try:
        with db_transaction.atomic():
            result = services.refund_donations(fundraise)
            fundraise.status = 'cancelled'
            fundraise.save(update_fields=['status'])
    except Exception:
        logger.exception('Ошибка отмены сбора #%s', pk)
        messages.error(request, 'Не удалось отменить сбор, попробуйте позже')
        return redirect('main:my_fundraises')

    if result['shortfall'] > 0:
        messages.warning(
            request,
            f'Сбор «{fundraise.title}» отменён. Донорам возвращено '
            f'{result["refunded"]} ₽, но {result["shortfall"]} ₽ вернуть не удалось: '
            f'на вашем балансе недостаточно средств. Задолженность зафиксирована.',
        )
    else:
        messages.warning(
            request,
            f'Сбор «{fundraise.title}» отменён, донорам возвращено {result["refunded"]} ₽.',
        )

    return redirect('main:my_fundraises')


# ==================== ПЛАТЕЖИ ====================

@login_required
@require_POST
def create_payment(request):
    """
    Создание платежа на пополнение через ЮKassa.

    Ветка криптовалюты удалена: приём цифровой валюты как встречного
    предоставления в РФ запрещён, и оба провайдера всё равно не
    инстанцировались (не реализованы абстрактные методы базового класса).
    """
    try:
        amount = parse_amount(request.POST.get('amount'), 'Сумма пополнения')
    except OperationRejected as exc:
        messages.error(request, str(exc))
        return redirect('main:topup')

    payment_method = request.POST.get('payment_method', 'card')
    allowed_methods = {choice[0] for choice in PaymentTransaction.METHOD_CHOICES}

    if amount < settings.MIN_TOPUP_AMOUNT:
        messages.error(request, f'Минимальная сумма пополнения — {settings.MIN_TOPUP_AMOUNT} ₽')
        return redirect('main:topup')

    if amount > settings.MAX_TOPUP_AMOUNT:
        messages.error(request, f'Максимальная сумма пополнения — {settings.MAX_TOPUP_AMOUNT} ₽')
        return redirect('main:topup')

    if payment_method not in allowed_methods:
        messages.error(request, 'Выбранный способ оплаты не поддерживается')
        return redirect('main:topup')

    # Дневной лимит пополнения по уровню верификации (115-ФЗ, ст. 10 161-ФЗ)
    allowed, limit_error = KYCService.check_payment_limit(request.user, amount)
    if not allowed:
        messages.error(request, limit_error)
        return redirect('main:topup')

    try:
        provider = YooKassaProvider()
        result = provider.create_payment(
            amount=amount,
            user_id=request.user.id,
            metadata={'payment_method': payment_method},
        )
    except Exception:
        # Детали исключения уходят в лог, пользователю — нейтральный текст:
        # раньше str(e) показывался прямо на странице
        logger.exception('Ошибка обращения к платёжной системе')
        messages.error(request, 'Платёжная система временно недоступна, попробуйте позже')
        return redirect('main:topup')

    if not result.get('success'):
        logger.warning('ЮKassa отказала в создании платежа: %s', result.get('error'))
        messages.error(request, 'Не удалось создать платёж. Попробуйте ещё раз.')
        return redirect('main:topup')

    PaymentTransaction.objects.create(
        user=request.user,
        amount=amount,
        payment_method=payment_method,
        payment_id=result['payment_id'],
        status='pending',
        metadata={'confirmation_url': result['confirmation_url']},
    )

    AntiFraudService(request.user).log_security_event(
        'payment', request,
        details={'amount': str(amount), 'payment_method': payment_method},
    )

    return redirect(result['confirmation_url'])


@csrf_exempt
@require_POST
def payment_webhook(request):
    """
    Уведомления ЮKassa о статусе платежа.

    Раньше эта вью зачисляла деньги по любому POST-запросу: подписи не было,
    IP не проверялся, сумма бралась из тела запроса. Обычным curl'ом можно
    было начислить себе произвольную сумму.

    Теперь тело уведомления используется только чтобы узнать, какой платёж
    перепроверить. Решение принимается по ответу API ЮKassa: статус и сумма
    берутся оттуда, а не из запроса.
    """
    import json

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

    event = body.get('event')
    payment_id = (body.get('object') or {}).get('id')

    try:
        confirmation = verify_yookassa_notification(request, payment_id)
    except WebhookRejected as exc:
        # 403, а не 500: для ЮKassa это сигнал не повторять доставку,
        # а для нас — строка в журнале безопасности.
        security_logger.warning('Отклонено уведомление о платеже %s: %s', payment_id, exc)
        return JsonResponse({'status': 'rejected'}, status=403)

    try:
        payment_tx = PaymentTransaction.objects.get(payment_id=payment_id)
    except PaymentTransaction.DoesNotExist:
        logger.warning('Уведомление о неизвестном платеже: %s', payment_id)
        # 200, чтобы ЮKassa не повторяла доставку бесконечно
        return JsonResponse({'status': 'ok'})

    confirmed_status = confirmation.get('status')
    confirmed_amount = confirmation.get('amount')

    if event == 'payment.succeeded' or confirmed_status == 'succeeded':
        if confirmed_status != 'succeeded':
            security_logger.warning(
                'Уведомление об успехе платежа %s, но API сообщает статус %s',
                payment_id, confirmed_status,
            )
            return JsonResponse({'status': 'ignored'})

        # Сумма из платёжной системы должна совпадать с тем, на что
        # пользователь оформил платёж. Расхождение — повод не зачислять.
        if confirmed_amount is not None and Decimal(confirmed_amount) != payment_tx.amount:
            security_logger.error(
                'Сумма платежа %s разошлась: в системе %s, у ЮKassa %s',
                payment_id, payment_tx.amount, confirmed_amount,
            )
            payment_tx.status = 'failed'
            payment_tx.save(update_fields=['status'])
            return JsonResponse({'status': 'amount_mismatch'}, status=409)

        if services.credit_payment(payment_tx):
            logger.info(
                'Платёж %s подтверждён, зачислено %s ₽ пользователю %s',
                payment_id, payment_tx.amount, payment_tx.user.username,
            )
        else:
            logger.info('Повторное уведомление о платеже %s — уже зачислен', payment_id)

    elif event == 'payment.canceled' or confirmed_status == 'canceled':
        if payment_tx.status not in ('paid', 'refunded'):
            payment_tx.status = 'cancelled'
            payment_tx.save(update_fields=['status'])
            logger.info('Платёж %s отменён', payment_id)

    return JsonResponse({'status': 'ok'})


@login_required
def payment_success(request):
    """
    Возврат пользователя из платёжной формы.

    Раньше страница безусловно сообщала «платёж прошёл, средства зачислены».
    Это было неправдой дважды: ЮKassa возвращает сюда и после отмены оплаты,
    а зачисление в любом случае происходит не здесь, а при получении
    уведомления от платёжной системы — то есть обычно чуть позже.

    Поэтому статус берётся из нашей же записи о платеже, а не из факта
    перехода по ссылке.
    """
    payment_id = (request.GET.get('payment_id') or '').strip()
    payment = None
    if payment_id:
        payment = PaymentTransaction.objects.filter(
            payment_id=payment_id, user=request.user,
        ).first()

    if payment is None:
        messages.info(
            request,
            'Платёж обрабатывается. Как только платёжная система подтвердит '
            'оплату, средства появятся на балансе.',
        )
    elif payment.status == 'paid':
        messages.success(request, f'✅ Платёж на {payment.amount} ₽ подтверждён, средства зачислены.')
    elif payment.status in ('cancelled', 'failed'):
        messages.warning(
            request,
            'Платёж не состоялся. Деньги не списаны — если средства всё же '
            'ушли, напишите в поддержку.',
        )
    else:
        messages.info(
            request,
            'Платёж обрабатывается. Средства появятся на балансе после '
            'подтверждения платёжной системой — обычно в течение нескольких минут.',
        )
    return redirect('main:dashboard')

@login_required
def payment_cancel(request):
    """Страница отмены оплаты"""
    messages.warning(request, '❌ Платеж был отменен. Попробуйте снова или выберите другой способ оплаты.')
    return redirect('main:topup')


# Крипто-вебхук, страница крипто-баланса и initiate_sbp_payment удалены.
# crypto_webhook брал сумму зачисления из тела запроса без какой-либо проверки
# подписи — подделкой одного POST начислялся любой рублёвый баланс.
# initiate_sbp_payment не был подключён к urls.py, ссылался на
# неопределённые переменные amount и fundraise и рендерил несуществующий
# шаблон payment_sbp.html.

# ==================== ДВУХФАКТОРНАЯ АУТЕНТИФИКАЦИЯ ====================

@login_required
def setup_2fa(request):
    """Настройка двухфакторной аутентификации"""
    two_factor, created = TwoFactorAuth.objects.get_or_create(user=request.user)
    
    if not two_factor.secret_key:
        two_factor.secret_key = TwoFactorAuthService.generate_secret()
        two_factor.save()
    
    if request.method == 'POST':
        code = request.POST.get('code')
        
        if TwoFactorAuthService.verify_code(two_factor.secret_key, code):
            two_factor.is_enabled = True
            two_factor.backup_codes = TwoFactorAuthService.generate_backup_codes()
            two_factor.save()
            
            AntiFraudService(request.user).log_security_event('2fa_enabled', request)
            
            messages.success(request, 'Двухфакторная аутентификация включена!')
            return redirect('main:profile')
        else:
            messages.error(request, 'Неверный код подтверждения')
    
    qr_code = TwoFactorAuthService.generate_qr_code(two_factor.secret_key, request.user.email)
    
    context = {
        'secret_key': two_factor.secret_key,
        'qr_code': qr_code,
        'backup_codes': two_factor.backup_codes if two_factor.is_enabled else None,
        'is_enabled': two_factor.is_enabled
    }
    return render(request, 'main/2fa_setup.html', context)


# ==================== ВЕРИФИКАЦИЯ (KYC) ====================

@login_required
@require_POST
def disable_2fa(request):
    """
    Выключение двухфакторной аутентификации.

    Включить её было можно, выключить — нет: ни вью, ни кнопки, при том что
    событие '2fa_disabled' в журнале безопасности предусмотрено с самого
    начала. Человек, сменивший телефон, оставался с включённой 2FA навсегда.

    Требуется действующий код или резервный: иначе выключить защиту сможет
    любой, кто добрался до открытой сессии.
    """
    two_factor = TwoFactorAuth.objects.filter(user=request.user, is_enabled=True).first()
    if two_factor is None:
        messages.error(request, 'Двухфакторная аутентификация не включена.')
        return redirect('main:setup_2fa')

    code = (request.POST.get('code') or '').strip()
    verified = (
        TwoFactorAuthService.verify_code(two_factor.secret_key, code)
        or TwoFactorAuthService.consume_backup_code(two_factor, code)
    )
    if not verified:
        messages.error(request, 'Неверный код. Введите код из приложения или резервный код.')
        return redirect('main:setup_2fa')

    two_factor.is_enabled = False
    two_factor.backup_codes = []
    two_factor.save(update_fields=['is_enabled', 'backup_codes'])

    AntiFraudService(request.user).log_security_event('2fa_disabled', request)
    messages.success(
        request,
        'Двухфакторная аутентификация выключена. Включить её снова можно здесь же.',
    )
    return redirect('main:profile')


@login_required
def verification_page(request):
    """Страница верификации пользователя"""
    verification, created = UserVerification.objects.get_or_create(user=request.user)
    documents = KYCDocument.objects.filter(user=request.user)
    limits = KYCService.get_verification_limits(verification.level)
    
    if request.method == 'POST':
        # Обработка данных верификации
        full_name = request.POST.get('full_name')
        birth_date = request.POST.get('birth_date')
        passport_series = request.POST.get('passport_series')
        passport_number = request.POST.get('passport_number')
        
        # Валидация
        name_error = KYCService.validate_name(full_name)
        if name_error:
            messages.error(request, name_error)
            return redirect('main:verification')
        
        passport_errors = KYCService.validate_passport({
            'passport_series': passport_series,
            'passport_number': passport_number
        })
        
        if passport_errors:
            for error in passport_errors.values():
                messages.error(request, error)
            return redirect('main:verification')

        # Дата рождения раньше присваивалась как есть: пустая строка роняла
        # страницу с 500 (ValidationError на уровне поля модели). Заодно
        # проверяется возраст: оферта (п. 3.1) и Политика ограничивают
        # сервис совершеннолетними, но нигде это не проверялось.
        parsed_birth_date = parse_birth_date(birth_date)
        if parsed_birth_date is None:
            messages.error(request, 'Укажите дату рождения в формате ДД.ММ.ГГГГ')
            return redirect('main:verification')

        age = years_since(parsed_birth_date)
        if age < 18:
            messages.error(
                request,
                'Сервис доступен только совершеннолетним: по указанной дате '
                'рождения вам меньше 18 лет.',
            )
            return redirect('main:verification')
        if age > 120:
            messages.error(request, 'Проверьте дату рождения — она указана неверно.')
            return redirect('main:verification')

        verification.full_name = full_name
        verification.birth_date = parsed_birth_date
        verification.passport_series = passport_series
        verification.passport_number = passport_number
        # Уровень верификации больше НЕ повышается самим фактом ввода данных.
        # Раньше строка `verification.level = 'basic'` давала уровень за
        # произвольные «1234/567890», прошедшие regex, — это была имитация
        # идентификации, а не идентификация.
        verification.submitted_at = timezone.now()
        verification.save(update_fields=[
            'full_name', 'birth_date', 'passport_series',
            'passport_number', 'submitted_at',
        ])

        AntiFraudService(request.user).log_security_event(
            'verification', request, details={'stage': 'data_submitted'},
        )

        messages.success(
            request,
            'Данные отправлены на проверку. Загрузите скан документа — '
            'уровень верификации повысится после проверки администратором.',
        )
        return redirect('main:verification')
    
    context = {
        'verification': verification,
        'documents': documents,
        'limits': limits,
        'verification_levels': dict(UserVerification.VERIFICATION_LEVELS)
    }
    return render(request, 'main/verification.html', context)


@login_required
@require_POST
def upload_document(request):
    """Загрузка документа для верификации."""
    document_type = request.POST.get('document_type')
    document_image = request.FILES.get('document_image')

    valid_types = {choice[0] for choice in KYCDocument.DOCUMENT_TYPES}
    if document_type not in valid_types:
        messages.error(request, 'Выберите тип документа')
        return redirect('main:verification')

    if not document_image:
        messages.error(request, 'Выберите файл')
        return redirect('main:verification')

    error = validate_uploaded_document(document_image)
    if error:
        messages.error(request, error)
        return redirect('main:verification')

    KYCDocument.objects.create(
        user=request.user,
        document_type=document_type,
        document_number=request.POST.get('document_number', ''),
        document_image=document_image,
        status='pending',
    )

    AntiFraudService(request.user).log_security_event(
        'verification', request, details={'stage': 'document_uploaded', 'type': document_type},
    )

    messages.success(request, 'Документ загружен на проверку')
    return redirect('main:verification')


@login_required
def kyc_document(request, pk):
    """
    Выдача скана документа.

    Файлы лежат в приватном хранилище вне MEDIA_ROOT, поэтому получить их
    можно только здесь и только владельцу либо сотруднику. Раньше документы
    складывались в media/ и при типовой конфигурации nginx оказались бы
    доступны по прямой ссылке кому угодно.
    """
    document = get_object_or_404(KYCDocument, pk=pk)
    is_owner = document.user_id == request.user.id

    if not (request.user.is_staff or is_owner):
        raise Http404

    # Открытие скана паспорта сотрудником — обращение к ПДн, и Политика
    # обещает, что оно журналируется. Django пишет в LogEntry изменения,
    # но не просмотры, поэтому без этой записи обещание было пустым.
    if not is_owner:
        PersonalDataAccessLog.record(
            actor=request.user,
            subject=document.user,
            data_type='kyc_document',
            reason='Проверка документов для верификации',
            request=request,
            object_repr=f'KYCDocument #{document.pk}',
        )

    try:
        handle = document.document_image.open('rb')
    except FileNotFoundError:
        raise Http404

    return FileResponse(handle, as_attachment=False)


@login_required
def search_users(request):
    """
    Поиск получателя перевода по имени пользователя.

    Поиск по email и выдача email убраны: эндпоинт отдавал адреса всех
    пользователей любому авторизованному и работал как выгрузка базы
    контактов по перебору подстрок.
    """
    query = (request.GET.get('q') or '').strip()
    if len(query) < 3:
        return JsonResponse({'users': []})

    users_list = User.objects.filter(
        username__istartswith=query
    ).exclude(id=request.user.id).order_by('username')[:10]

    return JsonResponse({'users': [{'username': u.username} for u in users_list]})


# ==================== ЮРИДИЧЕСКИЕ ДОКУМЕНТЫ ====================

def privacy_policy(request):
    """Политика обработки персональных данных (ч. 2 ст. 18.1 152-ФЗ)."""
    return render(request, 'main/privacy_policy.html')


def user_agreement(request):
    """Пользовательское соглашение — публичная оферта (ст. 437 ГК)."""
    return render(request, 'main/user_agreement.html')


def cookie_policy(request):
    """Политика использования cookies."""
    return render(request, 'main/cookie_policy.html')


def consent_processing(request):
    """
    Согласие на обработку ПДн — отдельный документ.

    Ч. 1 ст. 9 152-ФЗ с 01.09.2025 требует, чтобы согласие было оформлено
    отдельно от иных документов. Раньше оно было слито с офертой и политикой
    в три чекбокса одной формы.
    """
    return render(request, 'main/consent_processing.html')


def consent_distribution(request):
    """
    Согласие на распространение ПДн (ст. 10.1 152-ФЗ) — тоже отдельный
    документ, и оно необязательное: нужно только для публикации сбора.
    """
    return render(request, 'main/consent_distribution.html')


def legal_details(request):
    """Сведения о владельце сайта (ч. 2 ст. 10 149-ФЗ)."""
    return render(request, 'main/legal_details.html')


# ==================== ПРАВА СУБЪЕКТА ПДн ====================

@login_required
def export_my_data(request):
    """
    Выгрузка собственных данных в машиночитаемом виде.

    Ст. 14 152-ФЗ даёт право получить сведения об обработке; ст. 20 GDPR —
    право на переносимость. Раньше ни того, ни другого реализовано не было.

    Паспортные данные выгружаются расшифрованными — это данные самого
    пользователя, и он вправе их получить. Номер карты отдаётся маской:
    полный PAN не нужен ему самому и создаёт лишний носитель утечки.
    """
    import json

    user = request.user
    verification = getattr(user, 'verification', None)
    balance = getattr(user, 'balance', None)

    security_log_qs = SecurityLog.objects.filter(user=user).order_by('created_at')
    security_log_total = security_log_qs.count()
    security_logs = list(security_log_qs[:500])

    data = {
        'сформировано': timezone.now().isoformat(),
        'оператор': settings.OPERATOR['short_name'],
        'учётная_запись': {
            'имя_пользователя': user.username,
            'email': user.email,
            'дата_регистрации': user.date_joined.isoformat(),
            'последний_вход': user.last_login.isoformat() if user.last_login else None,
        },
        'баланс': str(balance.amount) if balance else '0.00',
        'верификация': {
            'уровень': verification.level if verification else 'unverified',
            'фио': verification.full_name if verification else None,
            'дата_рождения': (
                verification.birth_date.isoformat()
                if verification and verification.birth_date else None
            ),
            'паспорт_серия': verification.passport_series if verification else None,
            'паспорт_номер': verification.passport_number if verification else None,
            'адрес': verification.address if verification else None,
        },
        'согласия': [
            {
                'тип': consent.get_consent_type_display(),
                'версия': consent.version,
                'принято': consent.agreed_at.isoformat(),
                'отозвано': consent.revoked_at.isoformat() if consent.revoked_at else None,
                'ip': consent.ip_address,
            }
            for consent in UserConsent.objects.filter(user=user)
        ],
        'транзакции': [
            {
                'дата': tx.created_at.isoformat(),
                'тип': tx.get_kind_display(),
                'сумма': str(tx.amount),
                'от': tx.sender.username,
                'кому': tx.receiver.username,
                'комментарий': tx.comment,
                'статус': tx.get_status_display(),
            }
            for tx in Transaction.objects.filter(
                Q(sender=user) | Q(receiver=user)
            ).select_related('sender', 'receiver').order_by('created_at')
        ],
        'сборы': [
            {
                'название': f.title,
                'цель': str(f.target_amount),
                'собрано': str(f.current_amount),
                'статус': f.get_status_display(),
                'создан': f.created_at.isoformat(),
            }
            for f in Fundraise.objects.filter(author=user)
        ],
        'пожертвования': [
            {
                'дата': d.created_at.isoformat(),
                'сбор': d.fundraise.title,
                'сумма': str(d.amount),
                'анонимно': d.is_anonymous,
                'возвращено': str(d.refunded_amount) if d.refunded_amount else None,
            }
            for d in Donation.objects.filter(donor=user).select_related('fundraise')
        ],
        'заявки_на_вывод': [
            {
                'дата': w.created_at.isoformat(),
                'сумма': str(w.amount),
                'способ': w.get_payment_method_display(),
                'реквизиты': w.payment_details_masked,
                'статус': w.get_status_display(),
            }
            for w in WithdrawalRequest.objects.filter(user=user)
        ],
        'документы_верификации': [
            {
                'тип': doc.get_document_type_display(),
                'номер': doc.document_number,
                'файл': doc.document_image.name,
                'статус': doc.get_status_display(),
                'загружен': doc.uploaded_at.isoformat(),
            }
            for doc in KYCDocument.objects.filter(user=user)
        ],
        'журнал_безопасности': [
            {
                'дата': log.created_at.isoformat(),
                'действие': log.get_action_display(),
                'ip': log.ip_address,
                'браузер': log.user_agent,
                'подробности': log.details,
            }
            for log in security_logs
        ],
    }

    if security_log_total > len(security_logs):
        data['журнал_безопасности_примечание'] = (
            f'Показаны последние {len(security_logs)} записей из {security_log_total}. '
            f'Полную выгрузку можно запросить на {settings.OPERATOR["email"]}.'
        )

    AntiFraudService(user).log_security_event(
        'verification', request, details={'action': 'data_export'},
    )

    response = JsonResponse(data, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    # RFC 6266: кириллица в имени файла требует filename*, иначе Django
    # закодирует весь заголовок в base64 и браузер его не разберёт
    filename = f'angelsheart-data-{timezone.now():%Y%m%d}.json'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
def delete_account(request):
    """
    Удаление учётной записи по требованию пользователя.

    Раньше отзыв согласия только ставил флаг: паспорт, реквизиты и email
    оставались в базе. Ст. 14 152-ФЗ даёт право требовать уничтожения,
    ст. 17 GDPR — право на забвение.

    Что происходит: персональные данные уничтожаются, а сведения об
    операциях обезличиваются и сохраняются 5 лет — этого требует
    п. 4 ст. 7 115-ФЗ, и это прямо описано в Политике.
    """
    user = request.user

    # Расчёты должны быть завершены: удалять учётную запись с деньгами
    # на балансе нельзя — пользователь потеряет средства
    if user.balance.amount > 0:
        messages.error(
            request,
            f'На балансе {user.balance.amount} ₽. Выведите средства перед удалением.',
        )
        return redirect('main:my_consents')

    active_withdrawals = WithdrawalRequest.objects.filter(
        user=user, status__in=WithdrawalRequest.HOLDING_STATUSES,
    ).exists()
    if active_withdrawals:
        messages.error(request, 'Дождитесь завершения активных заявок на вывод.')
        return redirect('main:my_consents')

    active_fundraises = Fundraise.objects.filter(author=user, status='active')
    if active_fundraises.exists():
        messages.error(
            request,
            'У вас есть активный сбор. Завершите или отмените его — при отмене '
            'средства вернутся жертвователям.',
        )
        return redirect('main:my_consents')

    # Незавершённое пополнение: вебхук зачислил бы деньги на уже удалённый
    # аккаунт, и они стали бы недоступны никому
    pending_payments = PaymentTransaction.objects.filter(
        user=user, status__in=['pending', 'processing'],
    ).exists()
    if pending_payments:
        messages.error(
            request,
            'Есть незавершённый платёж. Дождитесь его зачисления или отмены.',
        )
        return redirect('main:my_consents')

    # Долг перед жертвователями по отменённому сбору (п. 7.4 оферты)
    unpaid_debt = Donation.objects.filter(
        fundraise__author=user, fundraise__status='cancelled', refunded_at__isnull=True,
    ).exists()
    if unpaid_debt:
        messages.error(
            request,
            'По отменённому сбору остался невозвращённый долг перед жертвователями. '
            'Погасите его в разделе «Задолженность» — после этого учётную запись '
            'можно будет удалить.',
        )
        return redirect('main:my_consents')

    confirmation = (request.POST.get('confirm') or '').strip()
    if confirmation != user.username:
        messages.error(request, 'Для подтверждения введите своё имя пользователя.')
        return redirect('main:my_consents')

    with db_transaction.atomic():
        # Логин вида deleted_<pk> можно занять заранее и тем самым навсегда
        # заблокировать человеку право на удаление (ст. 14 152-ФЗ).
        # uuid4 снимает и эту возможность, и любые коллизии.
        import uuid

        anonymous_label = f'deleted_{uuid.uuid4().hex[:12]}'

        # Паспортные данные и сканы документов уничтожаются
        UserVerification.objects.filter(user=user).delete()
        for document in KYCDocument.objects.filter(user=user):
            document.document_image.delete(save=False)
            document.delete()

        # Платёжные реквизиты стираются, заявки остаются обезличенными
        WithdrawalRequest.objects.filter(user=user).update(
            payment_details={}, payment_details_masked='удалено',
        )

        # Сборы снимаются с публикации: их описания могут содержать сведения
        # о здоровье и данные третьих лиц, а автора больше нет
        Fundraise.objects.filter(author=user).update(
            title='Сбор удалён', description='', image_url=None, is_featured=False,
        )

        # Документы к сборам — это справки и выписки, то есть ровно те
        # сведения, про которые пользователю сказано «уничтожены».
        # Без этого они оставались бы в приватном хранилище навсегда.
        for document in FundraiseDocument.objects.filter(fundraise__author=user):
            document.file.delete(save=False)
            document.delete()

        TwoFactorAuth.objects.filter(user=user).delete()
        PaymentTransaction.objects.filter(user=user).update(metadata={})

        # В журналах не остаётся ни связи с пользователем, ни его IP
        SecurityLog.objects.filter(user=user).update(
            user=None, user_agent='', ip_address=None, details={}, username_attempted='',
        )
        ConsentLog.objects.filter(user=user).update(ip_address=None, user_agent='')
        UserConsent.objects.filter(user=user).update(
            revoked_at=timezone.now(),
            revocation_reason='удаление учётной записи',
            is_accepted=False,
            ip_address=None,
            user_agent='',
        )

        # Токены доступа отзываются, иначе выданный JWT доживёт свой срок
        from rest_framework.authtoken.models import Token
        Token.objects.filter(user=user).delete()

        # Учётная запись обезличивается, но не удаляется: иначе каскадом
        # исчезнут транзакции, а их закон требует хранить 5 лет
        user.username = anonymous_label
        user.email = ''
        user.first_name = ''
        user.last_name = ''
        user.is_active = False
        user.set_unusable_password()
        user.save()

        logger.info('Учётная запись #%s удалена по требованию пользователя', user.pk)

    logout(request)
    messages.success(
        request,
        'Учётная запись удалена. Персональные данные уничтожены; сведения об '
        'операциях сохранены в обезличенном виде на срок, установленный законом.',
    )
    return redirect('main:login')

# ==================== МОДЕРАЦИЯ СБОРОВ ====================

def _staff_required(view):
    """Доступ только сотрудникам. Отсутствие прав — 404, а не 403."""
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404('Страница не найдена')
        return view(request, *args, **kwargs)
    return wrapper


@_staff_required
def moderation_queue(request):
    """
    Очередь заявок на проверку.

    По умолчанию показываются заявки в ожидании, в порядке поступления:
    сбор, поданный первым, и рассматривается первым — иначе выбор, что
    смотреть, остаётся на усмотрение сотрудника.
    """
    status = request.GET.get('status', 'pending')
    valid = dict(Fundraise.MODERATION_CHOICES)
    if status not in valid:
        status = 'pending'

    fundraises = (
        Fundraise.objects.filter(moderation_status=status)
        .select_related('author')
        .order_by('submitted_at' if status == 'pending' else '-moderated_at')
    )

    paginator = Paginator(fundraises, 25)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page,
        'fundraises': page.object_list,
        'selected_status': status,
        'statuses': Fundraise.MODERATION_CHOICES,
        'pending_count': Fundraise.objects.filter(moderation_status='pending').count(),
    }
    return render(request, 'main/moderation_queue.html', context)


@_staff_required
def moderate_fundraise(request, pk):
    """
    Карточка заявки: содержание сбора, автоматические признаки, решение.

    Решение принимает человек. Автоматика (moderation.detect_flags) только
    показывает, на что посмотреть: автоматический отказ по стоп-слову — это
    решение, значимо затрагивающее права субъекта, принятое исключительно
    на основании автоматизированной обработки (ст. 16 152-ФЗ).
    """
    fundraise = get_object_or_404(
        Fundraise.objects.select_related('author', 'moderated_by'), pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        comment = (request.POST.get('comment') or '').strip()[:2000]

        # Одобрить и вернуть на доработку можно только заявку, которая ждёт
        # решения: иначе заявка, поданная до отмены сбора, оставалась
        # в очереди и одобрялась уже после возврата денег донорам.
        #
        # Отклонение — другое дело: п. 7.6 оферты прямо позволяет снять
        # с публикации уже опубликованный сбор, и ждать новой заявки
        # для этого не требуется.
        if action in ('approve', 'request_changes') and fundraise.moderation_status != 'pending':
            messages.error(
                request,
                f'Заявка не ожидает решения: {fundraise.get_moderation_status_display()}.',
            )
            return redirect('main:moderation_queue')
        if fundraise.status in ('completed', 'cancelled'):
            messages.error(
                request,
                f'Сбор уже {fundraise.get_status_display().lower()} — решение по заявке '
                f'ничего не изменит.',
            )
            return redirect('main:moderation_queue')

        if action in ('request_changes', 'reject') and not comment:
            messages.error(request, 'Укажите причину: автор должен понимать, что исправлять.')
            return redirect('main:moderate_fundraise', pk=pk)

        if action == 'approve':
            # Проверки допуска повторяются перед одобрением: между отправкой
            # и решением автор мог отозвать согласие или удалить документ.
            problems = moderation.check_can_submit(fundraise)
            if problems:
                for problem in problems:
                    messages.error(request, problem)
                return redirect('main:moderate_fundraise', pk=pk)
            fundraise.approve(request.user, comment)
            messages.success(request, 'Сбор одобрен и опубликован.')
        elif action == 'request_changes':
            fundraise.request_changes(request.user, comment)
            messages.success(request, 'Заявка возвращена автору на доработку.')
        elif action == 'reject':
            # Отклонение возвращает уже собранное: сбор, снятый с публикации,
            # не вправе удерживать деньги жертвователей.
            with db_transaction.atomic():
                result = services.refund_donations(
                    fundraise, reason='сбор отклонён по результатам проверки',
                )
                fundraise.reject(request.user, comment)
            if result['count']:
                text = f'Сбор отклонён, возвращено {result["refunded"]} ₽ ({result["count"]} шт.).'
                if result['shortfall']:
                    text += f' Не хватило {result["shortfall"]} ₽ — долг автора перед жертвователями.'
                messages.success(request, text)
            else:
                messages.success(request, 'Сбор отклонён.')
        else:
            messages.error(request, 'Неизвестное действие.')
            return redirect('main:moderate_fundraise', pk=pk)

        return redirect('main:moderation_queue')

    context = {
        'fundraise': fundraise,
        'flags': moderation.detect_flags(fundraise),
        'problems': moderation.check_can_submit(fundraise),
        'documents': fundraise.documents.all(),
        'required_level_name': moderation.LEVEL_NAMES[
            moderation.required_verification_level(fundraise.target_amount)
        ],
        'author_level_name': moderation.LEVEL_NAMES[KYCService.get_level(fundraise.author)],
        'author_fundraises': Fundraise.objects.filter(
            author=fundraise.author,
        ).exclude(pk=fundraise.pk).order_by('-created_at')[:10],
    }
    return render(request, 'main/moderate_fundraise.html', context)


# ==================== ОГРАНИЧЕНИЯ ПО УЧЁТНЫМ ЗАПИСЯМ ====================

@login_required
def my_restrictions(request):
    """
    Страница ограничений: причина, сроки и форма объяснений.

    Раздел 9 оферты обещает пользователю право представить объяснения.
    Право, которое негде реализовать, правом не является — до этой
    страницы человек просто обнаруживал, что операции не проходят.
    """
    restrictions = AccountRestriction.objects.filter(user=request.user).select_related('decided_by')
    active = AccountRestriction.active_for(request.user)

    if request.method == 'POST':
        if active is None:
            messages.error(request, 'Действующих ограничений нет.')
            return redirect('main:my_restrictions')
        if active.appeal_submitted_at:
            messages.error(request, 'Объяснения уже поданы и рассматриваются.')
            return redirect('main:my_restrictions')

        text = (request.POST.get('appeal') or '').strip()
        if len(text) < 20:
            messages.error(request, 'Опишите обстоятельства подробнее — не менее 20 символов.')
            return redirect('main:my_restrictions')

        active.submit_appeal(text[:5000])
        messages.success(
            request,
            'Объяснения приняты. Оператор рассмотрит их в течение 5 рабочих дней '
            'и сообщит мотивированное решение.',
        )
        return redirect('main:my_restrictions')

    return render(request, 'main/my_restrictions.html', {
        'restrictions': restrictions,
        'active': active,
    })


@_staff_required
def restriction_queue(request):
    """Ограничения, ожидающие действия сотрудника."""
    pending_appeals = AccountRestriction.objects.filter(
        lifted_at__isnull=True, appeal_submitted_at__isnull=False, decision='',
    ).select_related('user').order_by('appeal_deadline')

    unnotified = AccountRestriction.objects.filter(
        lifted_at__isnull=True, notified_at__isnull=True,
    ).select_related('user').order_by('notify_deadline')

    active = AccountRestriction.objects.filter(
        lifted_at__isnull=True,
    ).select_related('user').order_by('-created_at')[:50]

    return render(request, 'main/restriction_queue.html', {
        'pending_appeals': pending_appeals,
        'unnotified': unnotified,
        'active': active,
    })


@_staff_required
@require_POST
def resolve_restriction(request, pk):
    """Решение сотрудника по возражению пользователя (п. 9.3)."""
    restriction = get_object_or_404(AccountRestriction, pk=pk)
    decision = request.POST.get('decision')
    comment = (request.POST.get('comment') or '').strip()[:2000]

    # Решение выносится по поданному возражению и один раз. Иначе журнал
    # показывает «рассмотрение объяснений» там, где объяснений не было,
    # а пользователь получает второе письмо с другим решением.
    if restriction.appeal_submitted_at is None:
        messages.error(
            request,
            'Пользователь не подавал объяснений. Снять ограничение без процедуры '
            'обжалования можно действием «Снять ограничение» в админ-панели.',
        )
        return redirect('main:restriction_queue')
    if restriction.decision:
        messages.error(request, 'Решение по этому возражению уже принято.')
        return redirect('main:restriction_queue')
    if restriction.lifted_at:
        messages.error(request, 'Ограничение уже снято.')
        return redirect('main:restriction_queue')

    if decision not in ('upheld', 'lifted'):
        messages.error(request, 'Выберите решение.')
        return redirect('main:restriction_queue')
    if not comment:
        # «Мотивированное решение» без мотивировки — это не решение
        messages.error(request, 'Мотивировка обязательна: её получает пользователь.')
        return redirect('main:restriction_queue')

    restriction.resolve(request.user, decision, comment)
    restrictions_service.notify_decision(restriction)
    messages.success(request, 'Решение принято и отправлено пользователю.')
    return redirect('main:restriction_queue')


@_staff_required
def breach_procedure(request):
    """
    Регламент реагирования на инциденты с ПДн.

    Внутренний документ, доступный сотрудникам: порядок действий, который
    нужно знать заранее. В сутки, когда инцидент случается, писать
    регламент поздно — а сроки ст. 21 152-ФЗ идут с момента, когда стало
    известно, а не с момента, когда разобрались.
    """
    return render(request, 'main/breach_procedure.html', {
        'legal_updated': settings.LEGAL_DOCS_UPDATED,
    })


# ==================== ПАРОЛЬ: ВОССТАНОВЛЕНИЕ И СМЕНА ====================

class PasswordResetRequestView(auth_views.PasswordResetView):
    """
    Запрос ссылки на восстановление пароля.

    Восстановления не было вовсе: форма регистрации требует email
    и объясняет это тем, что он нужен «для восстановления доступа»,
    но забывший пароль терял учётную запись навсегда — вместе с остатком
    на балансе. При включённой 2FA положение было ещё безнадёжнее.

    Письмо уходит и на несуществующий адрес тоже: ответ страницы не
    зависит от того, есть ли такой пользователь. Иначе форма превращается
    в проверку «зарегистрирован ли этот email», то есть в утечку.
    """

    template_name = 'main/password_reset.html'
    email_template_name = 'emails/password_reset.txt'
    html_email_template_name = 'emails/password_reset.html'
    subject_template_name = 'emails/password_reset_subject.txt'
    success_url = reverse_lazy('main:password_reset_done')

    def form_valid(self, form):
        # Попытки восстановления фиксируются: подбор адресов через эту
        # форму выглядит так же, как подбор пароля, и разбирать инцидент
        # без журнала нечем
        ip_address, user_agent = get_request_meta(self.request)
        SecurityLog.objects.create(
            action='password_change',
            username_attempted=form.cleaned_data.get('email', '')[:150],
            ip_address=ip_address,
            user_agent=user_agent,
            details={'event': 'password_reset_requested'},
        )
        return super().form_valid(form)


class PasswordResetSentView(auth_views.PasswordResetDoneView):
    template_name = 'main/password_reset_sent.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Ввод нового пароля по ссылке из письма."""

    template_name = 'main/password_reset_confirm.html'
    success_url = reverse_lazy('main:password_reset_complete')

    def form_valid(self, response):
        user = self.user
        ip_address, user_agent = get_request_meta(self.request)
        SecurityLog.objects.create(
            user=user, action='password_change',
            ip_address=ip_address, user_agent=user_agent,
            details={'event': 'password_reset_completed'},
        )
        return super().form_valid(response)


class PasswordResetFinishedView(auth_views.PasswordResetCompleteView):
    template_name = 'main/password_reset_complete.html'


class PasswordChangeView(auth_views.PasswordChangeView):
    """
    Смена известного пароля.

    Журнал безопасности с самого начала знал событие 'password_change',
    но сменить пароль было негде: ни вью, ни адреса, ни страницы.
    """

    template_name = 'main/password_change.html'
    success_url = reverse_lazy('main:password_change_done')

    def form_valid(self, form):
        ip_address, user_agent = get_request_meta(self.request)
        SecurityLog.objects.create(
            user=self.request.user, action='password_change',
            ip_address=ip_address, user_agent=user_agent,
            details={'event': 'password_changed'},
        )
        return super().form_valid(form)


class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = 'main/password_change_done.html'


# ==================== ПОДТВЕРЖДЕНИЕ АДРЕСА ПОЧТЫ ====================

def confirm_email(request, uidb64, token):
    """
    Переход по ссылке из письма.

    Вход не требуется: человек мог открыть письмо на другом устройстве,
    и требовать там пароль — верный способ сделать так, чтобы адрес
    никто не подтвердил.
    """
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None

    if user is None or not email_confirmation.token_generator.check_token(user, token):
        return render(request, 'main/email_confirm_result.html', {'success': False})

    confirmation = EmailConfirmation.for_user(user)
    already = confirmation.is_confirmed
    if not already:
        confirmation.confirm()
        AntiFraudService(user).log_security_event(
            'verification', request, details={'stage': 'email_confirmed'},
        )

    return render(request, 'main/email_confirm_result.html', {
        'success': True, 'already': already, 'confirmed_user': user,
    })


@login_required
@require_POST
def resend_email_confirmation(request):
    """Повторная отправка письма — с ограничением частоты."""
    confirmation = EmailConfirmation.for_user(request.user)

    # Проверяется фактическое подтверждение, а не отметка is_legacy:
    # пользователь, зарегистрированный до введения проверки, вправе
    # подтвердить адрес добровольно.
    if confirmation.is_actually_confirmed:
        messages.info(request, 'Адрес уже подтверждён.')
        return redirect('main:profile')

    if not request.user.email:
        messages.error(request, 'В учётной записи не указан адрес электронной почты.')
        return redirect('main:profile')

    if not confirmation.can_resend:
        # Иначе форма превращается в средство рассылки по чужим адресам
        messages.error(
            request,
            'Письмо уже отправлено. Повторить отправку можно через несколько минут — '
            'проверьте папку «Спам».',
        )
        return redirect('main:profile')

    if email_confirmation.send_confirmation(request.user, request):
        messages.success(request, f'Письмо отправлено на {request.user.email}.')
    else:
        messages.error(request, 'Не удалось отправить письмо. Попробуйте позже.')
    return redirect('main:profile')
