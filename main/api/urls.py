from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions
from . import views

# Swagger схема
# permission_classes нужен явно: public=True описывает только состав схемы,
# а доступ к самой странице оставался за настройками DRF — анонимный
# получал 401, то есть документация была недоступна тем, кому нужна.
schema_view = get_schema_view(
    openapi.Info(
        title="Ангел-Хранитель API",
        default_version='v1',
        description="API для мобильного приложения платформы взаимопомощи",
        contact=openapi.Contact(email="support@angel-helper.ru"),
        license=openapi.License(name="MIT License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

# Регистрация роутеров
router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='user')
router.register(r'fundraises', views.FundraiseViewSet, basename='fundraise')
router.register(r'donations', views.DonationViewSet, basename='donation')
router.register(r'transactions', views.TransactionViewSet, basename='transaction')
router.register(r'consents', views.ConsentViewSet, basename='consent')

urlpatterns = [
    # Документация
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    
    # Аутентификация
    path('auth/register/', views.RegisterView.as_view(), name='api_register'),
    path('auth/login/', views.LoginView.as_view(), name='api_login'),
    path('auth/logout/', views.LogoutView.as_view(), name='api_logout'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # Повторная отправка письма для подтверждения адреса: без него
    # недоступны вывод средств и публикация сбора
    path('auth/email/resend/', views.ResendEmailConfirmationView.as_view(),
         name='api_resend_email_confirmation'),
    
    # Статистика
    path('dashboard/', views.dashboard_stats, name='api_dashboard'),
    path('leaders/', views.leaders_board, name='api_leaders'),
    
    # API роутер
    path('', include(router.urls)),
]