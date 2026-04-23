from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'main'

urlpatterns = [
    # Аутентификация
    path('register/', views.register_page, name='register'),
    path('login/', views.login_page, name='login'),
    path('logout/', views.logout_page, name='logout'),
    
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
]