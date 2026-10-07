from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import views

# SimpleRouter, а не DefaultRouter: корень /api/ уже занят корневой
# страницей DefaultRouter из main.api, и вторая такая же перекрыла бы её
router = SimpleRouter()
router.register(r'people', views.PeopleViewSet, basename='people')
router.register(r'chats', views.ChatViewSet, basename='chat')
router.register(r'groups', views.CommunityViewSet, basename='community')
router.register(r'blocks', views.BlockViewSet, basename='block')

urlpatterns = [
    path('profile/', views.MyProfileView.as_view(), name='social_profile'),
    path('profile/avatar/', views.MyAvatarView.as_view(), name='social_avatar'),
    path('interests/', views.InterestListView.as_view(), name='social_interests'),
    path('', include(router.urls)),
]
