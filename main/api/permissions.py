"""
Права доступа для API.

Здесь остался один класс: IsOwnerOrReadOnly и IsNotAuthor не использовались
ни одним вьюсетом, а неиспользуемое правило доступа хуже отсутствующего —
при чтении кода кажется, что проверка где-то есть.
"""

from rest_framework import permissions


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Автор может изменять объект, остальные — только читать."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user
