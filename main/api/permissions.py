from rest_framework import permissions


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Разрешение: автор может редактировать, остальные только читать"""
    
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user


class IsOwnerOrReadOnly(permissions.BasePermission):
    """Разрешение: владелец может редактировать, остальные только читать"""
    
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.user == request.user


class IsNotAuthor(permissions.BasePermission):
    """Разрешение: пользователь не является автором сбора"""
    
    def has_object_permission(self, request, view, obj):
        return obj.author != request.user