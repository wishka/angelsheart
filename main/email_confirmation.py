"""
Отправка и проверка ссылки подтверждения адреса электронной почты.

Токен считается той же схемой, что и для восстановления пароля: подпись
от неизменяемых данных пользователя, без хранения токена в базе. Здесь
в подпись дополнительно входит сам адрес, поэтому ссылка, отправленная
на старый адрес, перестаёт работать после его смены.
"""

import logging

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.template.loader import TemplateDoesNotExist, render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from main.models import EmailConfirmation

logger = logging.getLogger('security')


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Токен, который перестаёт действовать при смене адреса или подтверждении."""

    def _make_hash_value(self, user, timestamp):
        confirmation = EmailConfirmation.objects.filter(user=user).first()
        confirmed_at = confirmation.confirmed_at if confirmation else None
        return f'{user.pk}{user.email}{confirmed_at}{timestamp}'


token_generator = EmailConfirmationTokenGenerator()


def send_confirmation(user, request=None):
    """
    Отправка письма со ссылкой подтверждения.

    Возвращает True, если письмо ушло. Отсутствие адреса или сбой отправки
    не считаются подтверждением: запись остаётся неподтверждённой.
    """
    if not user.email:
        return False

    confirmation = EmailConfirmation.for_user(user)
    path = reverse('main:confirm_email', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': token_generator.make_token(user),
    })
    link = f'{settings.SITE_URL.rstrip("/")}{path}'

    context = {'user': user, 'link': link, 'operator': settings.OPERATOR}
    fallback = (
        f'Здравствуйте, {user.username}!\n\n'
        f'Подтвердите адрес электронной почты, чтобы получать уведомления '
        f'об операциях и иметь возможность восстановить доступ к учётной записи:\n\n'
        f'{link}\n\n'
        f'До подтверждения недоступны вывод средств и публикация сбора.\n\n'
        f'Если вы не регистрировались в сервисе «Ангел-Хранитель», просто '
        f'не переходите по ссылке — учётная запись без подтверждения '
        f'не сможет распоряжаться деньгами.'
    )

    html_message = None
    try:
        html_message = render_to_string('emails/email_confirmation.html', context)
    except TemplateDoesNotExist:
        logger.warning('Шаблон письма подтверждения не найден, отправляю текстовое письмо')

    try:
        send_mail(
            subject='Подтвердите адрес электронной почты',
            message=fallback,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception:
        logger.exception('Не удалось отправить письмо подтверждения пользователю %s', user.pk)
        return False

    confirmation.mark_sent()
    return True
