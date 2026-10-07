"""
Лента: подписки, публикации, лайки, комментарии, жалобы.

Правила видимости в одном месте (visible_posts): лента, профиль
человека, карточка поста, лайк и комментарий — все проверяют доступ
через него, и разойтись между собой не могут.
"""

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from . import images
from .models import (
    ChatMember, ContentReport, Follow, MessageReport, Post, PostComment, PostLike,
)
from .services import (
    REPORTS_TO_HIDE, SocialError, blocked_by_ids, blocked_ids, block_user,
    discoverable_users, has_distribution_consent, is_blocked_between,
)

PAGE_SIZE = 20


# ==================== ПОДПИСКИ ====================

def can_follow(viewer, user):
    """Подписаться можно на того, кого видно: открытая анкета или общий чат."""
    if viewer.pk == user.pk or not user.is_active or is_blocked_between(viewer, user):
        return False
    if discoverable_users().filter(pk=user.pk).exists():
        return True
    return ChatMember.objects.filter(user=viewer, chat__members__user=user).exists()


def set_follow(viewer, user, follow):
    if follow:
        if not can_follow(viewer, user):
            raise SocialError('Подписаться на этого пользователя нельзя', status=403)
        Follow.objects.get_or_create(follower=viewer, following=user)
        return f'Вы подписались на {user.username}'
    Follow.objects.filter(follower=viewer, following=user).delete()
    return f'Вы отписались от {user.username}'


def follow_counts(user):
    return {
        'followers_count': Follow.objects.filter(following=user).count(),
        'following_count': Follow.objects.filter(follower=user).count(),
        'posts_count': Post.objects.filter(author=user, is_hidden=False).count(),
    }


# ==================== ВИДИМОСТЬ ====================

def visible_posts(viewer):
    """
    Все публикации, которые viewer имеет право видеть:
      свои; публичные авторов с действующим согласием на распространение;
      «для подписчиков» — тех, на кого viewer подписан.
    Минус скрытые модератором и всё, где чёрный список с любой стороны.
    """
    hidden_authors = blocked_ids(viewer) | blocked_by_ids(viewer)
    public_authors = discoverable_users().values('pk')
    followed = Follow.objects.filter(follower=viewer).values('following_id')
    return (
        Post.objects
        .filter(
            Q(author=viewer)
            | Q(visibility=Post.VISIBILITY_PUBLIC, author__in=public_authors)
            | Q(author__in=followed)
        )
        .filter(Q(is_hidden=False) | Q(author=viewer))
        .exclude(author__in=hidden_authors)
        .filter(author__is_active=True)
    )


def annotate_for(queryset, viewer):
    return queryset.select_related('author', 'author__social_profile').annotate(
        likes_total=Count('likes', distinct=True),
        comments_total=Count('comments', filter=Q(comments__is_hidden=False), distinct=True),
        liked_by_me=Exists(PostLike.objects.filter(post=OuterRef('pk'), user=viewer)),
    )


def page(queryset, before=None, size=PAGE_SIZE):
    """
    Страница по курсору «до id». Курсор, а не номер страницы: в ленту
    постоянно добавляются записи, и при номерах страниц следующая
    страница повторяла бы уже показанные посты.
    """
    if before:
        queryset = queryset.filter(id__lt=before)
    items = list(queryset.order_by('-id')[:size + 1])
    has_more = len(items) > size
    items = items[:size]
    return items, (items[-1].pk if has_more and items else None)


def feed(viewer, scope='following', before=None):
    posts = visible_posts(viewer)
    if scope == 'following':
        followed = Follow.objects.filter(follower=viewer).values('following_id')
        posts = posts.filter(Q(author=viewer) | Q(author__in=followed))
    else:  # 'all' — публичные посты всех, кого можно видеть
        posts = posts.filter(Q(visibility=Post.VISIBILITY_PUBLIC) | Q(author=viewer))
    return page(annotate_for(posts, viewer), before)


def posts_of(viewer, user, before=None):
    return page(annotate_for(visible_posts(viewer).filter(author=user), viewer), before)


def get_post(viewer, post_id):
    post = annotate_for(visible_posts(viewer), viewer).filter(pk=post_id).first()
    if post is None:
        raise SocialError('Публикация не найдена', status=404)
    return post


# ==================== ПУБЛИКАЦИИ ====================

def create_post(author, text, image=None, visibility=Post.VISIBILITY_FOLLOWERS):
    text = (text or '').strip()
    if not text and image is None:
        raise SocialError('Публикация пустая')
    if len(text) > Post.MAX_LENGTH:
        raise SocialError(f'Текст длиннее {Post.MAX_LENGTH} символов')
    if visibility not in dict(Post.VISIBILITY_CHOICES):
        visibility = Post.VISIBILITY_FOLLOWERS
    notice = ''
    if visibility == Post.VISIBILITY_PUBLIC and not has_distribution_consent(author):
        visibility = Post.VISIBILITY_FOLLOWERS
        notice = ('Публикация видна только подписчикам: чтобы публиковать для всех, '
                  'откройте анкету для поиска — это согласие на распространение данных.')

    prepared = None
    if image is not None:
        try:
            prepared = images.prepare(image, images.CHAT_SIDE)
        except images.ImageRejected as error:
            raise SocialError(error.message)

    post = Post(author=author, text=text, visibility=visibility)
    if prepared is not None:
        post.image.save(prepared.name, prepared, save=False)
    post.save()
    return post, notice


def edit_post(author, post_id, text=None, visibility=None):
    post = Post.objects.filter(pk=post_id, author=author).first()
    if post is None:
        raise SocialError('Редактировать можно только свою публикацию', status=404)
    if text is not None:
        text = text.strip()
        if not text and not post.image:
            raise SocialError('Публикация пустая')
        post.text = text[:Post.MAX_LENGTH]
    if visibility in dict(Post.VISIBILITY_CHOICES):
        if visibility == Post.VISIBILITY_PUBLIC and not has_distribution_consent(author):
            raise SocialError('Публиковать для всех можно с открытой для поиска анкетой')
        post.visibility = visibility
    post.edited_at = timezone.now()
    post.save()
    return post


def delete_post(author, post_id):
    post = Post.objects.filter(pk=post_id, author=author).first()
    if post is None:
        raise SocialError('Удалить можно только свою публикацию', status=404)
    if post.image:
        post.image.delete(save=False)
    post.delete()


def set_like(viewer, post_id, liked):
    post = get_post(viewer, post_id)
    if liked:
        PostLike.objects.get_or_create(post=post, user=viewer)
    else:
        PostLike.objects.filter(post=post, user=viewer).delete()
    return get_post(viewer, post_id)


# ==================== КОММЕНТАРИИ ====================

def comments_of(viewer, post_id, before=None):
    post = get_post(viewer, post_id)
    hidden_authors = blocked_ids(viewer) | blocked_by_ids(viewer)
    comments = (
        PostComment.objects.filter(post=post)
        .filter(Q(is_hidden=False) | Q(author=viewer))
        .exclude(author__in=hidden_authors)
        .select_related('author', 'author__social_profile', 'post')
    )
    return page(comments, before, size=50)


def add_comment(viewer, post_id, text):
    post = get_post(viewer, post_id)
    text = (text or '').strip()
    if not text:
        raise SocialError('Комментарий пустой')
    if len(text) > PostComment.MAX_LENGTH:
        raise SocialError(f'Комментарий длиннее {PostComment.MAX_LENGTH} символов')
    return PostComment.objects.create(post=post, author=viewer, text=text)


def delete_comment(viewer, post_id, comment_id):
    """Удалить может автор комментария и автор публикации."""
    comment = PostComment.objects.filter(pk=comment_id, post_id=post_id).select_related('post').first()
    if comment is None or viewer.pk not in (comment.author_id, comment.post.author_id):
        raise SocialError('Комментарий не найден', status=404)
    comment.delete()


# ==================== ЖАЛОБЫ ====================

def report(viewer, post_id, reason, comment_id=None, text='', also_block=False):
    post = get_post(viewer, post_id)
    target = post
    if comment_id:
        target = PostComment.objects.filter(pk=comment_id, post=post).first()
        if target is None:
            raise SocialError('Комментарий не найден', status=404)
    if target.author_id == viewer.pk:
        raise SocialError('Нельзя пожаловаться на свою публикацию')
    if reason not in dict(MessageReport.REASON_CHOICES):
        raise SocialError('Укажите причину жалобы')

    lookup = {'post': post, 'comment': target if comment_id else None, 'reporter': viewer}
    with transaction.atomic():
        if ContentReport.objects.filter(**lookup).exists():
            raise SocialError('Вы уже пожаловались')
        ContentReport.objects.create(
            **lookup, reason=reason, comment_text=(text or '').strip()[:500],
            content_text=target.text + (' [фото]' if comment_id is None and post.image else ''),
            author=target.author,
        )
        reporters = ContentReport.objects.filter(
            post=post, comment=lookup['comment'], status=MessageReport.STATUS_NEW,
        ).values('reporter').distinct().count()
        if reporters >= REPORTS_TO_HIDE and not target.is_hidden:
            target.is_hidden = True
            target.save(update_fields=['is_hidden'])
        result = 'Жалоба отправлена. Модератор рассмотрит её.'
        if also_block:
            block_user(viewer, target.author)
            result += f' {target.author.username} добавлен в чёрный список.'
    return result


def resolve_content_reports(report, moderator, accept):
    target = report.target
    if target is None:
        return
    with transaction.atomic():
        ContentReport.objects.filter(
            post_id=report.post_id, comment_id=report.comment_id, status=MessageReport.STATUS_NEW,
        ).update(
            status=MessageReport.STATUS_ACCEPTED if accept else MessageReport.STATUS_REJECTED,
            resolved_at=timezone.now(), resolved_by=moderator,
        )
        target.is_hidden = accept
        target.save(update_fields=['is_hidden'])


# ==================== УДАЛЕНИЕ УЧЁТНОЙ ЗАПИСИ ====================

def erase_user_feed(user):
    """Публикации, лайки, комментарии и подписки человека удаляются целиком."""
    for post in Post.objects.filter(author=user).exclude(image=''):
        post.image.delete(save=False)
    Post.objects.filter(author=user).delete()
    PostComment.objects.filter(author=user).delete()
    PostLike.objects.filter(user=user).delete()
    Follow.objects.filter(Q(follower=user) | Q(following=user)).delete()
