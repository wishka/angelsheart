from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import JsonResponse
from .models import Balance, Transaction
from .forms import RegisterForm, TransferForm


# ==================== АУТЕНТИФИКАЦИЯ ====================

def register_page(request):
    """Регистрация нового пользователя"""
    if request.user.is_authenticated:
        return redirect('main:dashboard')
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Добро пожаловать, {user.username}!')
            return redirect('main:dashboard')
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
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            
            # Настройка "Запомнить меня"
            if not remember_me:
                # Если не выбран "Запомнить меня" - сессия закроется при закрытии браузера
                request.session.set_expiry(0)
            else:
                # Если выбран - сессия будет жить 30 дней
                request.session.set_expiry(settings.SESSION_REMEMBER_ME_AGE)
            
            messages.success(request, f'С возвращением, {user.username}!')
            return redirect('main:dashboard')
        else:
            messages.error(request, 'Неверное имя пользователя или пароль')
    
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
    """Пополнение баланса"""
    if request.method == 'POST':
        try:
            amount = float(request.POST.get('amount', 0))
            if amount <= 0:
                messages.error(request, 'Сумма должна быть больше 0')
                return redirect('main:topup')
            
            if amount > 100000:
                messages.error(request, 'Максимальная сумма пополнения 100 000 ₽')
                return redirect('main:topup')
            
            with db_transaction.atomic():
                balance = Balance.objects.select_for_update().get(user=request.user)
                balance.amount += amount
                balance.save()
                
                Transaction.objects.create(
                    sender=request.user,
                    receiver=request.user,
                    amount=amount,
                    comment='Пополнение баланса',
                    status='completed'
                )
            
            messages.success(request, f'💰 Баланс пополнен на {amount} ₽')
            return redirect('main:dashboard')
        
        except ValueError:
            messages.error(request, 'Введите корректную сумму')
    
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
    """Проверка существования username"""
    username = request.GET.get('username', '')
    if username:
        exists = User.objects.filter(username=username).exists()
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

def privacy_policy(request):
    """Страница политики конфиденциальности"""
    return render(request, 'main/privacy_policy.html')

def user_agreement(request):
    """Страница пользовательского соглашения"""
    return render(request, 'main/user_agreement.html')

def cookie_policy(request):
    """Страница политики использования cookies"""
    return render(request, 'main/cookie_policy.html')