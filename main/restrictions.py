"""
Применение, уведомление и снятие ограничений по учётным записям.

Раздел 9 оферты обещает пользователю три вещи: он узнает о приостановлении
и его причине в течение 1 рабочего дня, он вправе представить объяснения,
и они будут рассмотрены в течение 5 рабочих дней с мотивированным ответом.
До этого модуля не выполнялась ни одна из трёх: блокировка сводилась
к is_active=False, и человек просто обнаруживал, что не может войти.

Уведомление отправляется в момент применения ограничения. Если письмо
не ушло, запись остаётся неуведомлённой и попадает в очередь ограничений
(«Ограничения: очередь») с отметкой о пропущенном сроке: срок, который
никто не заметил, нарушается молча.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import TemplateDoesNotExist, render_to_string

from main.models import AccountRestriction, SecurityLog

logger = logging.getLogger('security')


def apply_restriction(user, kind, ground, reason, actor=None, internal_note=''):
    """
    Применение ограничения с немедленной попыткой уведомить пользователя.

    Причина обязательна и пишется для человека: «нарушение правил» ничего
    не объясняет и не позволяет возразить по существу, а право возразить
    обещано п. 9.3.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('Причина ограничения обязательна: её видит пользователь')

    restriction = AccountRestriction.objects.create(
        user=user, kind=kind, ground=ground, reason=reason,
        internal_note=internal_note, created_by=actor,
    )

    SecurityLog.objects.create(
        user=user, action='suspicious',
        details={
            'event': 'restriction_applied',
            'kind': kind,
            'ground': ground,
            'restriction_id': restriction.pk,
            'actor': getattr(actor, 'username', None),
        },
    )

    notify(restriction)
    return restriction


def notify(restriction):
    """
    Уведомление пользователя о применённом ограничении (п. 9.2).

    Возвращает True, если письмо ушло. Пользователь без email остаётся
    неуведомлённым — и запись об этом сохраняется: считать уведомлённым
    того, кому некуда написать, значит скрыть собственное нарушение срока.
    """
    user = restriction.user
    if not user.email:
        logger.warning(
            'Ограничение #%s: у пользователя %s нет email, уведомление невозможно',
            restriction.pk, user.username,
        )
        return False

    context = {
        'restriction': restriction,
        'user': user,
        'site_url': settings.SITE_URL,
        'operator': settings.OPERATOR,
    }
    fallback = (
        f'{restriction.get_kind_display()}.\n\n'
        f'Причина: {restriction.reason}\n\n'
        f'Вы вправе представить объяснения и документы. Оператор рассмотрит их '
        f'в течение 5 рабочих дней и сообщит мотивированное решение '
        f'(п. 9.3 Пользовательского соглашения).\n\n'
        f'Подать объяснения: {settings.SITE_URL}/restrictions/\n\n'
        f'Ограничение не влечёт утрату права на средства: остаток остаётся за вами '
        f'(п. 9.4 Пользовательского соглашения).'
    )

    html_message = None
    try:
        html_message = render_to_string('emails/restriction_applied.html', context)
    except TemplateDoesNotExist:
        logger.warning('Шаблон письма об ограничении не найден, отправляю текстовое письмо')

    try:
        send_mail(
            subject='Операции по вашей учётной записи ограничены',
            message=fallback,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception:
        logger.exception('Не удалось уведомить об ограничении #%s', restriction.pk)
        return False

    restriction.mark_notified()
    return True


def notify_decision(restriction):
    """Мотивированный ответ на возражение пользователя (п. 9.3)."""
    user = restriction.user
    if not user.email:
        return False

    fallback = (
        f'Решение по вашим объяснениям: {restriction.get_decision_display()}.\n\n'
        f'{restriction.decision_comment}'
    )
    html_message = None
    try:
        html_message = render_to_string('emails/restriction_decision.html', {
            'restriction': restriction,
            'user': user,
            'site_url': settings.SITE_URL,
            'operator': settings.OPERATOR,
        })
    except TemplateDoesNotExist:
        pass

    try:
        send_mail(
            subject='Решение по ограничению учётной записи',
            message=fallback,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception:
        logger.exception('Не удалось отправить решение по ограничению #%s', restriction.pk)
        return False
    return True
