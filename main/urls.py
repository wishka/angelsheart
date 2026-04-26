from django.urls import path, include
from django.contrib.auth import views as auth_views
from . import views

app_name = 'main'

urlpatterns = [
    # Аутентификация
    path('register/', views.register_page, name='register'),
    path('login/', views.login_page, name='login'),
    path('logout/', views.logout_page, name='logout'),
    # API для мобильного приложения
    path('api/', include('main.api.urls')),
    
    # Основные страницы
    path('', views.dashboard, name='dashboard'),
    path('transfer/', views.transfer_money, name='transfer'),
    path('history/', views.transaction_history, name='history'),
    path('topup/', views.top_up_balance, name='topup'),
    path('profile/', views.user_profile, name='profile'),
    path('profile/<str:username>/', views.user_profile, name='user_profile'),
    path('leaders/', views.leaders_board, name='leaders'),
    path('quick-help/', views.quick_help, name='quick_help'),
    
    # AJAX endpoints
    path('api/check-username/', views.check_username, name='check_username'),
    path('api/get-balance/', views.get_balance_json, name='get_balance_json'),
    path('api/cancel/<int:transaction_id>/', views.cancel_transaction, name='cancel_transaction'),
    
    # Документы и согласия
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('user-agreement/', views.user_agreement, name='user_agreement'),
    path('cookie-policy/', views.cookie_policy, name='cookie_policy'),
    path('my-consents/', views.my_consents, name='my_consents'),
    
    # Сборы средств
    path('fundraises/', views.fundraise_list, name='fundraises'),
    path('fundraise/<int:pk>/', views.fundraise_detail, name='fundraise_detail'),
    path('fundraise/create/', views.create_fundraise, name='create_fundraise'),
    path('my-fundraises/', views.my_fundraises, name='my_fundraises'),
    path('my-donations/', views.my_donations, name='my_donations'),
    path('fundraise/<int:pk>/complete/', views.complete_fundraise, name='complete_fundraise'),
    path('fundraise/<int:pk>/cancel/', views.cancel_fundraise, name='cancel_fundraise'),
    path('withdrawal/', views.create_withdrawal_request, name='withdrawal'),
    path('my-withdrawals/', views.my_withdrawals, name='my_withdrawals'),
    path('withdrawal/<int:pk>/cancel/', views.cancel_withdrawal, name='cancel_withdrawal'),
]