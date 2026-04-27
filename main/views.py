from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import Balance, Transaction, Fundraise, Donation, UserConsent, ConsentLog, WithdrawalRequest
from .forms import RegisterForm, TransferForm, FundraiseForm, DonationForm, WithdrawalForm
from django.core.cache import cache
from decimal import Decimal
from main.payments.yookassa import YooKassaProvider, MockPaymentProvider, SBPProvider, StripeProvider
from main.payments.security import TwoFactorAuthService, AntiFraudService
from main.payments.verification import KYCService
from main.models import PaymentTransaction, TwoFactorAuth, SecurityLog, UserVerification, KYCDocument
import logging

logger = logging.getLogger('withdrawals')
security_logger = logging.getLogger('security')

# ==================== АУТЕНТИФИКАЦИЯ ====================

def register_page(request):
    """Регистрация нового пользователя с сохранением согласий"""
    if request.user.is_authenticated:
        return redirect('main:dashboard')
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        
        # Получаем согласия из формы
        agree_privacy = request.POST.get('agree_privacy')
        agree_terms = request.POST.get('agree_terms')
        agree_cookies = request.POST.get('agree_cookies')
        
        # Проверка всех обязательных согласий
        if not agree_privacy or not agree_terms or not agree_cookies:
            messages.error(request, 'Вы должны принять все условия для регистрации')
            return render(request, 'main/register.html', {'form': form})
        
        if form.is_valid():
            user = form.save()
            
            # Получаем IP-адрес пользователя
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0]
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            
            # Сохраняем согласия
            consents_data = [
                ('privacy', agree_privacy, 'Политика конфиденциальности', '1.0'),
                ('terms', agree_terms, 'Пользовательское соглашение', '1.0'),
                ('cookies', agree_cookies, 'Политика cookies', '1.0'),
            ]
            
            for consent_type, agreed, _, version in consents_data:
                if agreed:
                    # Создаем запись согласия
                    consent = UserConsent.objects.create(
                        user=user,
                        consent_type=consent_type,
                        version=version,
                        is_accepted=True,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        agreed_at=timezone.now()
                    )
                    
                    # Создаем лог
                    ConsentLog.objects.create(
                        user=user,
                        action='accept',
                        consent_type=consent_type,
                        version=version,
                        ip_address=ip_address,
                        user_agent=user_agent
                    )
                    
                    print(f"Создано согласие: {consent_type} для пользователя {user.username}")  # Для отладки
            
            # Сохраняем в сессию информацию о согласиях
            request.session['agreed_to_privacy'] = True
            request.session['agreed_to_terms'] = True
            request.session['agreed_to_cookies'] = True
            request.session['consent_version'] = '1.0'
            request.session['consent_signed_at'] = timezone.now().isoformat()
            
            login(request, user)
            messages.success(request, f'Добро пожаловать, {user.username}!')
            return redirect('main:dashboard')
        else:
            for error in form.errors.values():
                messages.error(request, error)
    else:
        form = RegisterForm()
    
    return render(request, 'main/register.html', {'form': form})


def login_page(request):
    """Авторизация пользователя с функцией "Запомнить меня" """
    if request.user.is_authenticated:
        return redirect('main:dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        remember_me = request.POST.get('remember_me')
        
        print(f"Login attempt: {username}, remember_me={remember_me}")  # Отладка
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            
            # Настройка "Запомнить меня"
            if remember_me == 'on':
                # 30 дней в секундах
                request.session.set_expiry(30 * 24 * 60 * 60)
                print(f"Session expiry set to 30 days for {username}")
            else:
                # Сессия закроется при закрытии браузера
                request.session.set_expiry(0)
                print(f"Session expiry set to browser close for {username}")
            
            messages.success(request, f'С возвращением, {user.username}!')
            return redirect('main:dashboard')
        else:
            messages.error(request, 'Неверное имя пользователя или пароль')
            print(f"Failed login attempt for {username}")
    
    return render(request, 'main/login.html')


@login_required
def logout_page(request):
    """Выход из системы"""
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
                receiver = User.objects.get(username=receiver_username)
            except User.DoesNotExist:
                messages.error(request, f'Пользователь "{receiver_username}" не найден')
                return redirect('main:transfer')
            
            if receiver == request.user:
                messages.error(request, 'Нельзя перевести деньги самому себе')
                return redirect('main:transfer')
            
            if request.user.balance.amount < amount:
                messages.error(request, f'Недостаточно средств. Ваш баланс: {request.user.balance.amount} ₽')
                return redirect('main:transfer')
            
            try:
                with db_transaction.atomic():
                    sender_balance = Balance.objects.select_for_update().get(user=request.user)
                    receiver_balance = Balance.objects.select_for_update().get(user=receiver)
                    
                    if sender_balance.amount < amount:
                        raise ValueError("Недостаточно средств")
                    
                    Transaction.objects.create(
                        sender=request.user,
                        receiver=receiver,
                        amount=amount,
                        comment=comment,
                        status='completed'
                    )
                    
                    sender_balance.amount -= amount
                    sender_balance.save()
                    receiver_balance.amount += amount
                    receiver_balance.save()
                    
                    messages.success(request, f'✅ Перевод {amount} ₽ пользователю {receiver.username} выполнен!')
                    return redirect('main:dashboard')
            
            except Exception as e:
                messages.error(request, f'Ошибка при переводе: {str(e)}')
                return redirect('main:transfer')
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
    """Пополнение баланса через платежную систему"""
    
    # Определяем, использовать реальные платежи или симуляцию
    USE_REAL_PAYMENTS = getattr(settings, 'USE_REAL_PAYMENTS', False)
    
    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', '0'))
        
        if amount <= 0:
            messages.error(request, 'Сумма должна быть больше 0')
            return redirect('main:topup')
        
        if amount > Decimal('100000'):
            messages.error(request, 'Максимальная сумма пополнения 100 000 ₽')
            return redirect('main:topup')
        
        if USE_REAL_PAYMENTS:
            # Реальный платеж через ЮKassa
            try:
                provider = YooKassaProvider()
                
                result = provider.create_payment(
                    amount=amount,
                    user_id=request.user.id,
                    metadata={
                        'payment_method': 'card',
                        'user_email': request.user.email
                    }
                )
                
                if result['success']:
                    # Сохраняем информацию о платеже
                    PaymentTransaction.objects.create(
                        user=request.user,
                        amount=amount,
                        payment_method='card',
                        payment_id=result['payment_id'],
                        status='pending',
                        metadata={
                            'confirmation_url': result['confirmation_url']
                        }
                    )
                    
                    # Редирект на страницу оплаты
                    return redirect(result['confirmation_url'])
                else:
                    messages.error(request, f'Ошибка: {result.get("error", "Не удалось создать платеж")}')
                    return redirect('main:topup')
            
            except Exception as e:
                messages.error(request, f'Ошибка подключения к платежной системе: {str(e)}')
                return redirect('main:topup')
        
        else:
            # Симуляция пополнения (для разработки)
            with db_transaction.atomic():
                balance = Balance.objects.select_for_update().get(user=request.user)
                balance.amount += amount
                balance.save()
                
                Transaction.objects.create(
                    sender=request.user,
                    receiver=request.user,
                    amount=amount,
                    comment='Пополнение баланса (тестовый режим)',
                    status='completed'
                )
            
            messages.success(request, f'💰 Баланс пополнен на {amount} ₽ (тестовый режим)')
            return redirect('main:dashboard')
    
    return render(request, 'main/topup.html')


@login_required
def user_profile(request, username=None):
    """Профиль пользователя"""
    if username:
        profile_user = get_object_or_404(User, username=username)
        is_own_profile = (profile_user == request.user)
    else:
        profile_user = request.user
        is_own_profile = True
    
    total_sent = Transaction.objects.filter(
        sender=profile_user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    total_received = Transaction.objects.filter(
        receiver=profile_user, status='completed'
    ).aggregate(Sum('amount'))['amount__sum'] or 0
    
    transactions_count = Transaction.objects.filter(
        Q(sender=profile_user) | Q(receiver=profile_user),
        status='completed'
    ).count()
    
    recent_transactions = Transaction.objects.filter(
        Q(sender=profile_user) | Q(receiver=profile_user)
    ).select_related('sender', 'receiver').order_by('-created_at')[:5]
    
    context = {
        'profile_user': profile_user,
        'is_own_profile': is_own_profile,
        'balance': profile_user.balance.amount,
        'total_sent': total_sent,
        'total_received': total_received,
        'transactions_count': transactions_count,
        'member_since': profile_user.date_joined,
        'recent_transactions': recent_transactions,
    }
    return render(request, 'main/profile.html', context)


@login_required
def leaders_board(request):
    """Рейтинг пользователей"""
    top_senders = User.objects.filter(
        sent_transactions__status='completed'
    ).annotate(
        total_sent=Sum('sent_transactions__amount')
    ).filter(
        total_sent__isnull=False
    ).order_by('-total_sent')[:10]
    
    top_receivers = User.objects.filter(
        received_transactions__status='completed'
    ).annotate(
        total_received=Sum('received_transactions__amount')
    ).filter(
        total_received__isnull=False
    ).order_by('-total_received')[:10]
    
    context = {
        'top_senders': top_senders,
        'top_receivers': top_receivers,
    }
    return render(request, 'main/leaders.html', context)


@login_required
def quick_help(request):
    """Быстрая помощь"""
    active_users = User.objects.exclude(id=request.user.id).filter(
        received_transactions__isnull=False
    ).distinct()
    
    if not active_users.exists():
        messages.info(request, 'Пока нет активных пользователей для помощи')
        return redirect('main:dashboard')
    
    import random
    random_user = random.choice(list(active_users))
    
    context = {
        'suggested_user': random_user,
        'suggested_amount': 50,
        'current_balance': request.user.balance.amount,
    }
    return render(request, 'main/quick_help.html', context)


# ==================== API ENDPOINTS ====================

@login_required
def check_username(request):
    """Проверка существования username (не чувствительна к регистру)"""
    username = request.GET.get('username', '')
    if username:
        # icontains делает поиск нечувствительным к регистру
        exists = User.objects.filter(username__icontains=username).exists()
        return JsonResponse({'exists': exists, 'username': username})
    return JsonResponse({'exists': False})


@login_required
def get_balance_json(request):
    """Получение текущего баланса"""
    return JsonResponse({
        'balance': float(request.user.balance.amount),
        'username': request.user.username,
    })


@login_required
def cancel_transaction(request, transaction_id):
    """Отмена транзакции"""
    transaction = get_object_or_404(Transaction, id=transaction_id, sender=request.user)
    
    if transaction.status == 'pending':
        transaction.status = 'failed'
        transaction.save()
        messages.success(request, 'Транзакция отменена')
    else:
        messages.error(request, 'Невозможно отменить эту транзакцию')
    
    return redirect('main:history')


# ==================== СБОРЫ СРЕДСТВ ====================

@login_required
def fundraise_list(request):
    """Страница со списком сборов"""
    category = request.GET.get('category', 'all')
    status = request.GET.get('status', 'active')
    
    fundraises = Fundraise.objects.all()
    
    if category != 'all':
        fundraises = fundraises.filter(category=category)
    if status != 'all':
        fundraises = fundraises.filter(status=status)
    
    # Статистика
    total_fundraises = fundraises.count()
    total_raised = fundraises.aggregate(Sum('current_amount'))['current_amount__sum'] or 0
    total_donors = Donation.objects.values('donor').distinct().count()
    
    context = {
        'fundraises': fundraises,
        'total_fundraises': total_fundraises,
        'total_raised': total_raised,
        'total_donors': total_donors,
        'selected_category': category,
        'selected_status': status,
    }
    return render(request, 'main/fundraises.html', context)


@login_required
def fundraise_detail(request, pk):
    """Детальная страница сбора"""
    fundraise = get_object_or_404(Fundraise, pk=pk)
    donations = fundraise.donations.all()[:20]
    
    # Последние пожертвования
    recent_donations = fundraise.donations.select_related('donor').order_by('-created_at')[:10]
    
    # Проверка, является ли пользователь автором сбора
    is_author = (request.user == fundraise.author)
    
    if request.method == 'POST':
        # Запрещаем автору жертвовать самому себе
        if is_author:
            messages.error(request, '❌ Вы не можете пожертвовать средства в свой собственный сбор')
            return redirect('main:fundraise_detail', pk=pk)
        
        form = DonationForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            message = form.cleaned_data.get('message', '')
            is_anonymous = form.cleaned_data.get('is_anonymous', False)
            
            # Проверка минимальной суммы
            if amount < 1:
                messages.error(request, 'Минимальная сумма пожертвования - 1 ₽')
                return redirect('main:fundraise_detail', pk=pk)
            
            # Проверка баланса
            if request.user.balance.amount < amount:
                messages.error(request, f'Недостаточно средств. Ваш баланс: {request.user.balance.amount} ₽')
                return redirect('main:fundraise_detail', pk=pk)
            
            try:
                with db_transaction.atomic():
                    # Обновляем баланс пользователя (донора) - списываем полную сумму
                    user_balance = Balance.objects.select_for_update().get(user=request.user)
                    user_balance.amount -= amount
                    user_balance.save()
                    
                    # Обновляем баланс автора сбора - зачисляем полную сумму (без комиссии!)
                    author_balance = Balance.objects.select_for_update().get(user=fundraise.author)
                    author_balance.amount += amount
                    author_balance.save()
                    
                    # Обновляем сумму сбора
                    fundraise.current_amount += amount
                    fundraise.donors_count += 1
                    fundraise.save()
                    
                    # Создаем пожертвование
                    donation = Donation.objects.create(
                        donor=request.user,
                        fundraise=fundraise,
                        amount=amount,
                        message=message,
                        is_anonymous=is_anonymous
                    )
                    
                    # Создаем транзакцию (полная сумма!)
                    transaction_comment = f'Пожертвование на сбор "{fundraise.title}"'
                    if message:
                        transaction_comment += f' - "{message[:50]}"'
                    
                    Transaction.objects.create(
                        sender=request.user,
                        receiver=fundraise.author,
                        amount=amount,  # Полная сумма, без вычета комиссии
                        comment=transaction_comment,
                        status='completed',
                        is_donation=True,
                        fundraise_id=fundraise.id
                    )
                    
                    messages.success(
                        request,
                        f'✅ Спасибо за пожертвование! '
                        f'{amount} ₽ отправлено в сбор "{fundraise.title}".'
                    )
                    return redirect('main:fundraise_detail', pk=pk)
            
            except Exception as e:
                messages.error(request, f'Ошибка при пожертвовании: {str(e)}')
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
        'commission_percent': 0,  # Комиссия 0%
    }
    return render(request, 'main/fundraise_detail.html', context)


@login_required
def create_fundraise(request):
    """Создание нового сбора (только если нет активных сборов)"""
    # Проверяем, есть ли у пользователя активный сбор
    has_active_fundraise = Fundraise.objects.filter(author=request.user, status='active').exists()
    
    if has_active_fundraise:
        messages.error(request, 'У вас уже есть активный сбор. Завершите или отмените его, прежде чем создавать новый.')
        return redirect('main:my_fundraises')
    
    if request.method == 'POST':
        form = FundraiseForm(request.POST)
        if form.is_valid():
            fundraise = form.save(commit=False)
            fundraise.author = request.user
            fundraise.save()
            messages.success(request, f'Сбор "{fundraise.title}" успешно создан!')
            return redirect('main:fundraise_detail', pk=fundraise.pk)
    else:
        form = FundraiseForm()
    
    return render(request, 'main/create_fundraise.html', {'form': form})


@login_required
def my_fundraises(request):
    """Мои сборы"""
    active_fundraises = Fundraise.objects.filter(author=request.user, status='active')
    completed_fundraises = Fundraise.objects.filter(author=request.user, status='completed')
    cancelled_fundraises = Fundraise.objects.filter(author=request.user, status='cancelled')
    
    # Проверяем, есть ли активный сбор
    has_active_fundraise = active_fundraises.exists()
    
    context = {
        'active_fundraises': active_fundraises,
        'completed_fundraises': completed_fundraises,
        'cancelled_fundraises': cancelled_fundraises,
        'has_active_fundraise': has_active_fundraise,
    }
    return render(request, 'main/my_fundraises.html', context)


@login_required
def my_donations(request):
    """Мои пожертвования"""
    donations = Donation.objects.filter(donor=request.user).select_related('fundraise').order_by('-created_at')
    
    context = {
        'donations': donations,
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
        
        if action == 'revoke':
            # Отзыв согласия
            consent = UserConsent.objects.filter(
                user=request.user,
                consent_type=consent_type,
                is_accepted=True,
                revoked_at__isnull=True
            ).first()
            
            if consent:
                reason = request.POST.get('reason', '')
                consent.revoke(reason)
                
                # Логируем отзыв
                ConsentLog.objects.create(
                    user=request.user,
                    action='revoke',
                    consent_type=consent_type,
                    version=consent.version,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )
                
                messages.info(request, f'Согласие на "{consent.get_consent_type_display()}" отозвано')
        
        elif action == 'reaccept':
            # Повторное принятие согласия
            version = request.POST.get('version', '1.0')
            
            # Создаем новое согласие
            UserConsent.objects.create(
                user=request.user,
                consent_type=consent_type,
                version=version,
                is_accepted=True,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            # Логируем принятие
            ConsentLog.objects.create(
                user=request.user,
                action='accept',
                consent_type=consent_type,
                version=version,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            messages.success(request, f'Согласие на "{dict(UserConsent.CONSENT_TYPES).get(consent_type)}" принято')
        
        return redirect('main:my_consents')
    
    context = {
        'consents': consents,
        'consent_logs': consent_logs,
    }
    return render(request, 'main/my_consents.html', context)


@login_required
def complete_fundraise(request, pk):
    """Завершение сбора автором"""
    fundraise = get_object_or_404(Fundraise, pk=pk, author=request.user)
    
    if fundraise.status == 'active':
        fundraise.status = 'completed'
        fundraise.save()
        
        # Создаем уведомление о завершении
        messages.success(request,
                         f'Сбор "{fundraise.title}" успешно завершен! Все собранные средства ({fundraise.current_amount | floatformat:2} ₽) зачислены на ваш баланс.')
        
        # Здесь можно добавить логику для перевода средств на отдельный счет
        # Но так как средства уже поступают сразу на баланс автора,
        # дополнительных действий не требуется
    
    else:
        messages.error(request, 'Этот сбор уже завершен или отменен')
    
    return redirect('main:my_fundraises')


@login_required
def create_withdrawal_request(request):
    """Создание заявки на вывод средств"""
    if request.method == 'POST':
        form = WithdrawalForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            
            # Проверка минимальной суммы
            if amount < settings.MIN_WITHDRAWAL_AMOUNT:
                messages.error(request, f'Минимальная сумма вывода - {settings.MIN_WITHDRAWAL_AMOUNT} ₽')
                return redirect('main:withdrawal')
            
            if amount > settings.MAX_WITHDRAWAL_AMOUNT:
                messages.error(request, f'Максимальная сумма вывода - {settings.MAX_WITHDRAWAL_AMOUNT} ₽')
                return redirect('main:withdrawal')
            
            # Проверка баланса
            if request.user.balance.amount < amount:
                messages.error(request, 'Недостаточно средств')
                return redirect('main:withdrawal')
            
            # Проверка на дубликаты (3 заявки в день)
            today_requests = WithdrawalRequest.objects.filter(
                user=request.user,
                created_at__date=timezone.now().date(),
                status__in=['pending', 'processing']
            ).count()
            
            if today_requests >= 3:
                messages.error(request, 'Вы можете создать не более 3 заявок в день')
                return redirect('main:withdrawal')
            
            # Получаем реквизиты
            payment_method = form.cleaned_data['payment_method']
            payment_details = {}
            
            if payment_method == 'card':
                payment_details = {
                    'card_number': form.cleaned_data.get('card_number'),
                    'card_holder': form.cleaned_data.get('card_holder'),
                    'expiry_date': form.cleaned_data.get('expiry_date'),
                }
            elif payment_method == 'sbp':
                payment_details = {
                    'phone_number': form.cleaned_data.get('phone_number'),
                }
            elif payment_method == 'yoomoney':
                payment_details = {
                    'wallet_number': form.cleaned_data.get('wallet_number'),
                }
            
            try:
                with db_transaction.atomic():
                    # Блокируем средства
                    balance = Balance.objects.select_for_update().get(user=request.user)
                    balance.amount -= amount
                    balance.save()
                    
                    # Создаем заявку
                    withdrawal = WithdrawalRequest.objects.create(
                        user=request.user,
                        amount=amount,
                        payment_method=payment_method,
                        payment_details=payment_details
                    )
                    
                    logger.info(f"Создана заявка #{withdrawal.id} на вывод {amount} ₽ от {request.user.username}")
                    
                    messages.success(
                        request,
                        f'Заявка на вывод {amount} ₽ создана. Статус: {withdrawal.get_status_display()}'
                    )
                    return redirect('main:my_withdrawals')
            
            except Exception as e:
                logger.error(f"Ошибка при создании заявки: {str(e)}")
                messages.error(request, 'Ошибка при создании заявки')
                return redirect('main:withdrawal')
    else:
        form = WithdrawalForm()
    
    context = {
        'form': form,
        'current_balance': request.user.balance.amount,
        'min_amount': settings.MIN_WITHDRAWAL_AMOUNT,
        'max_amount': settings.MAX_WITHDRAWAL_AMOUNT,
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
def cancel_withdrawal(request, pk):
    """Отмена заявки пользователем"""
    withdrawal = get_object_or_404(WithdrawalRequest, pk=pk, user=request.user)
    
    if withdrawal.cancel():
        messages.success(request, f'Заявка #{withdrawal.id} отменена, средства возвращены на баланс')
    else:
        messages.error(request, 'Невозможно отменить заявку в текущем статусе')
    
    return redirect('main:my_withdrawals')

@login_required
def cancel_fundraise(request, pk):
    """Отмена сбора автором"""
    fundraise = get_object_or_404(Fundraise, pk=pk, author=request.user)
    
    if fundraise.status == 'active':
        fundraise.status = 'cancelled'
        fundraise.save()
        messages.warning(request, f'Сбор "{fundraise.title}" отменен.')
        
        # Здесь можно добавить логику возврата средств донатерам
        # Но для простоты оставим как есть - средства уже на счету автора
    
    else:
        messages.error(request, 'Этот сбор уже завершен или отменен')
    
    return redirect('main:my_fundraises')


# ==================== ПЛАТЕЖИ ====================

@login_required
def create_payment(request):
    """Создание платежа на пополнение"""
    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount', '0'))
        payment_method = request.POST.get('payment_method', 'card')
        
        # Проверка суммы
        if amount < 100:
            messages.error(request, 'Минимальная сумма пополнения - 100 ₽')
            return redirect('main:topup')
        
        # Выбор провайдера
        if payment_method == 'card':
            provider = YooKassaProvider()
        elif payment_method == 'stripe':
            provider = StripeProvider()
        elif payment_method == 'sbp':
            provider = SBPProvider()
        else:
            provider = YooKassaProvider()
        
        # Создание платежа
        result = provider.create_payment(
            amount=amount,
            user_id=request.user.id,
            metadata={'payment_method': payment_method}
        )
        
        if result['success']:
            # Сохраняем платеж в БД
            PaymentTransaction.objects.create(
                user=request.user,
                amount=amount,
                payment_method=payment_method,
                payment_id=result['payment_id'],
                status='pending',
                metadata={'confirmation_url': result['confirmation_url']}
            )
            
            # Логируем
            AntiFraudService(request.user).log_security_event('payment', request, {
                'amount': amount,
                'payment_method': payment_method
            })
            
            # Редирект на оплату
            return redirect(result['confirmation_url'])
        else:
            messages.error(request, f'Ошибка: {result.get("error", "Неизвестная ошибка")}')
            return redirect('main:topup')
    
    return redirect('main:topup')


@csrf_exempt
def payment_webhook(request):
    """
    Webhook для обработки статусов платежей от ЮKassa
    """
    import json
    
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
    
    try:
        # Получаем тело запроса
        body = json.loads(request.body)
        
        # Проверяем тип события
        event = body.get('event')
        
        if event == 'payment.succeeded':
            payment_id = body.get('object', {}).get('id')
            
            try:
                # Находим платеж в нашей системе
                payment_tx = PaymentTransaction.objects.get(payment_id=payment_id)
                
                if payment_tx.status != 'paid':
                    # Обновляем статус
                    payment_tx.status = 'paid'
                    payment_tx.paid_at = timezone.now()
                    payment_tx.save()
                    
                    # Начисляем баланс пользователю
                    with db_transaction.atomic():
                        balance = Balance.objects.select_for_update().get(user=payment_tx.user)
                        balance.amount += payment_tx.amount
                        balance.save()
                        
                        # Создаем транзакцию
                        Transaction.objects.create(
                            sender=payment_tx.user,
                            receiver=payment_tx.user,
                            amount=payment_tx.amount,
                            comment='Пополнение баланса через ЮKassa',
                            status='completed'
                        )
                    
                    # Логируем успешный платеж
                    logger.info(f"Payment succeeded: #{payment_id} for user {payment_tx.user.username}")
            
            except PaymentTransaction.DoesNotExist:
                logger.warning(f"Payment not found: {payment_id}")
        
        elif event == 'payment.canceled':
            payment_id = body.get('object', {}).get('id')
            try:
                payment_tx = PaymentTransaction.objects.get(payment_id=payment_id)
                payment_tx.status = 'cancelled'
                payment_tx.save()
                logger.info(f"Payment cancelled: #{payment_id}")
            except PaymentTransaction.DoesNotExist:
                pass
        
        return JsonResponse({'status': 'ok'})
    
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def payment_success(request):
    """Страница успешной оплаты"""
    messages.success(request, '✅ Ваш платеж успешно прошел! Средства зачислены на баланс.')
    return redirect('main:dashboard')

@login_required
def payment_cancel(request):
    """Страница отмены оплаты"""
    messages.warning(request, '❌ Платеж был отменен. Попробуйте снова или выберите другой способ оплаты.')
    return redirect('main:topup')

@login_required
def initiate_sbp_payment(request, fundraise_id):
    # ... получаете fundraise и сумму ...
    provider = YooKassaProvider()
    result = provider.create_sbp_qr_payment(amount, order_id=str(fundraise_id), description=fundraise.title)

    if result['success']:
        # Отдаем QR-код в шаблон страницы оплаты
        return render(request, 'main/payment_sbp.html', {'qr_code': result['qr_code']})
    else:
        messages.error(request, 'Ошибка при создании платежа. Попробуйте позже.')
        return redirect('main:fundraise_detail', pk=fundraise_id)
    
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
        
        verification.full_name = full_name
        verification.birth_date = birth_date
        verification.passport_series = passport_series
        verification.passport_number = passport_number
        verification.level = 'basic'  # После заполнения данных - базовая верификация
        verification.verified_at = timezone.now()
        verification.save()
        
        AntiFraudService(request.user).log_security_event('verification', request)
        
        messages.success(request, 'Данные верификации сохранены!')
        return redirect('main:verification')
    
    context = {
        'verification': verification,
        'documents': documents,
        'limits': limits,
        'verification_levels': dict(UserVerification.VERIFICATION_LEVELS)
    }
    return render(request, 'main/verification.html', context)


@login_required
def upload_document(request):
    """Загрузка документа для верификации"""
    if request.method == 'POST':
        document_type = request.POST.get('document_type')
        document_image = request.FILES.get('document_image')
        
        if not document_image:
            messages.error(request, 'Выберите файл')
            return redirect('main:verification')
        
        KYCDocument.objects.create(
            user=request.user,
            document_type=document_type,
            document_number=request.POST.get('document_number', ''),
            document_image=document_image,
            status='pending'
        )
        
        messages.success(request, 'Документ загружен на проверку')
        return redirect('main:verification')
    
    return redirect('main:verification')


@login_required
def search_users(request):
    """API поиск пользователей по username/email (нечувствительный к регистру)"""
    query = request.GET.get('q', '').strip()
    users = []
    
    if len(query) >= 3:
        from django.contrib.auth.models import User
        from django.db.models import Q
        
        # Поиск без учета регистра
        users_list = User.objects.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
        ).exclude(id=request.user.id)[:10]
        
        # Формируем результат с оригинальным регистром username
        users = [{'username': u.username, 'email': u.email} for u in users_list]
    
    return JsonResponse({'users': users})

def privacy_policy(request):
    """Страница политики конфиденциальности"""
    return render(request, 'main/privacy_policy.html')

def user_agreement(request):
    """Страница пользовательского соглашения"""
    return render(request, 'main/user_agreement.html')

def cookie_policy(request):
    """Страница политики использования cookies"""
    return render(request, 'main/cookie_policy.html')