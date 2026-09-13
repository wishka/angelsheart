from django.urls import path, include

from . import views

app_name = 'main'

urlpatterns = [
    # Аутентификация
    path('register/', views.register_page, name='register'),
    path('login/', views.login_page, name='login'),
    path('login/2fa/', views.login_2fa, name='login_2fa'),
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
    path('api/search-users/', views.search_users, name='search_users'),
    
    # Документы и согласия
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('user-agreement/', views.user_agreement, name='user_agreement'),
    path('cookie-policy/', views.cookie_policy, name='cookie_policy'),
    # Согласия по ст. 9 и ст. 10.1 152-ФЗ оформляются отдельными документами
    path('consent/processing/', views.consent_processing, name='consent_processing'),
    path('consent/distribution/', views.consent_distribution, name='consent_distribution'),
    # ч. 2 ст. 10 149-ФЗ: сведения о владельце сайта
    path('legal/', views.legal_details, name='legal_details'),
    path('my-consents/', views.my_consents, name='my_consents'),
    path('my-consents/export/', views.export_my_data, name='export_my_data'),
    path('my-consents/delete-account/', views.delete_account, name='delete_account'),
    
    # Сборы средств
    path('fundraises/', views.fundraise_list, name='fundraises'),
    path('fundraise/<int:pk>/', views.fundraise_detail, name='fundraise_detail'),
    path('fundraise/create/', views.create_fundraise, name='create_fundraise'),
    path('my-fundraises/', views.my_fundraises, name='my_fundraises'),
    path('my-donations/', views.my_donations, name='my_donations'),
    path('fundraise/<int:pk>/complete/', views.complete_fundraise, name='complete_fundraise'),
    path('fundraise/<int:pk>/cancel/', views.cancel_fundraise, name='cancel_fundraise'),

    # Подготовка сбора и модерация
    path('fundraise/<int:pk>/manage/', views.fundraise_manage, name='fundraise_manage'),
    path('fundraise/<int:pk>/submit/', views.submit_fundraise, name='submit_fundraise'),
    path('fundraise/<int:pk>/documents/upload/', views.upload_fundraise_document,
         name='upload_fundraise_document'),
    path('fundraise-document/<int:pk>/', views.fundraise_document, name='fundraise_document'),
    path('moderation/', views.moderation_queue, name='moderation_queue'),
    path('moderation/<int:pk>/', views.moderate_fundraise, name='moderate_fundraise'),

    # Ограничения по учётным записям (раздел 9 оферты)
    path('restrictions/', views.my_restrictions, name='my_restrictions'),
    path('restrictions/queue/', views.restriction_queue, name='restriction_queue'),
    path('restrictions/<int:pk>/resolve/', views.resolve_restriction, name='resolve_restriction'),

    # Регламент реагирования на инциденты с ПДн (ст. 21 152-ФЗ)
    path('breach-procedure/', views.breach_procedure, name='breach_procedure'),

    # Вывод средств
    path('withdrawal/', views.create_withdrawal_request, name='withdrawal'),
    path('my-withdrawals/', views.my_withdrawals, name='my_withdrawals'),
    path('withdrawal/<int:pk>/cancel/', views.cancel_withdrawal, name='cancel_withdrawal'),
    
    # Платежи
    path('payment/create/', views.create_payment, name='create_payment'),
    path('payment/webhook/', views.payment_webhook, name='payment_webhook'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/cancel/', views.payment_cancel, name='payment_cancel'),

    # Безопасность
    path('2fa/setup/', views.setup_2fa, name='setup_2fa'),

    # Верификация
    path('verification/', views.verification_page, name='verification'),
    path('verification/upload/', views.upload_document, name='upload_document'),
    path('verification/document/<int:pk>/', views.kyc_document, name='kyc_document'),
]