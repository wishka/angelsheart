"""
Ограничение доступа при блокировке учётной записи.

Модель AccountRestriction различала два вида ограничения — приостановление
операций и блокировку учётной записи, — но код читал только сам факт
ограничения. Заблокированный пользователь спокойно пользовался сервисом:
не мог лишь потратить деньги. Оферта (п. 9.1) и Политика (п. 11.1, возраст
младше 18 лет) обещают именно блокировку учётной записи, и обещание надо
либо исполнять, либо не давать.

Блокировка не превращается в исчезновение человека из системы: ему
остаются доступны страница ограничений (иначе право возразить по п. 9.3
реализовать негде), юридические документы, выгрузка собственных данных
и выход из учётной записи.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve, reverse


# Что доступно при блокировке. Именно эти имена, а не префиксы путей:
# префикс легко разъезжается с urls.py и молча открывает лишнее.
ALLOWED_VIEW_NAMES = frozenset({
    'my_restrictions',      # право представить объяснения (п. 9.3 оферты)
    'logout',
    'login',
    'login_2fa',
    'privacy_policy',
    'user_agreement',
    'cookie_policy',
    'consent_processing',
    'consent_distribution',
    'legal_details',
    'my_consents',          # отзыв согласия — право по ст. 9 152-ФЗ
    'export_my_data',       # право на доступ к своим данным — ст. 14 152-ФЗ
    # Удаление учётной записи (ст. 14 152-ФЗ, п. 12.1 оферты — «в любой
    # момент»). Вью сама откажет при ненулевом балансе, незакрытых заявках
    # и долге перед жертвователями, так что блокировать её незачем, а
    # заблокировать значило бы лишить человека права, обещанного законом.
    'delete_account',
    # Собственные документы, приложенные к сбору: это данные самого
    # пользователя, вью пускает к ним только владельца и сотрудника.
    'fundraise_document',
    # Погашение долга перед жертвователями и пополнение баланса для него.
    # Без них получался замкнутый круг: погасить нельзя (страница закрыта),
    # пополнить нельзя, а удалить учётную запись не даёт непогашенный долг.
    'my_debt',
    'topup',
    'create_payment',
    'payment_success',
    'payment_cancel',
})


class AccountBlockMiddleware:
    """Пропускает заблокированного пользователя только к разрешённым страницам."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or user.is_staff:
            return self.get_response(request)

        from main.models import AccountRestriction

        restriction = AccountRestriction.active_for(user)
        if restriction is None or restriction.kind != 'blocked':
            return self.get_response(request)

        # API отвечает кодом, а не перенаправлением на HTML-страницу:
        # мобильный клиент из редиректа ничего не поймёт.
        if request.path_info.startswith('/api/'):
            from django.http import JsonResponse

            return JsonResponse(
                {'error': 'Учётная запись заблокирована', 'reason': restriction.reason},
                status=403,
            )

        try:
            match = resolve(request.path_info)
        except Exception:
            return self.get_response(request)

        # Админка и статика решают доступ сами
        if match.app_name not in ('main', ''):
            return self.get_response(request)
        if match.url_name in ALLOWED_VIEW_NAMES:
            return self.get_response(request)

        messages.error(
            request,
            f'Учётная запись заблокирована: {restriction.reason} '
            f'Представить объяснения можно на этой странице.',
        )
        return redirect(reverse('main:my_restrictions'))
