"""
Push-уведомления через Firebase Cloud Messaging (HTTP v1).

Что уходит в Google: только номер чата. Ни имени отправителя, ни текста
сообщения в уведомлении нет — получив его, приложение само спрашивает
наш сервер, что пришло, и уже на телефоне собирает уведомление. Иначе
текст переписки проходил бы через серверы Google за рубежом, а это
трансграничная передача персональных данных (ст. 12 152-ФЗ) со всеми
её обязанностями.

Включается двумя переменными окружения:
  FCM_PROJECT_ID        — идентификатор проекта Firebase
  FCM_CREDENTIALS_FILE  — путь к JSON-ключу сервисного аккаунта
Без них уведомления не отправляются, а всё остальное работает как раньше.

Отправка идёт в отдельном потоке после фиксации транзакции: запрос
к Google занимает сотни миллисекунд, и человек не должен ждать его,
нажав «Отправить».
"""

import logging
import threading

from django.conf import settings
from django.db import transaction

from .models import ChatMember, DeviceToken, UserBlock

logger = logging.getLogger('social.push')

SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'

_credentials = None
_credentials_lock = threading.Lock()


def is_enabled():
    return bool(getattr(settings, 'FCM_PROJECT_ID', '') and getattr(settings, 'FCM_CREDENTIALS_FILE', ''))


def _access_token():
    """OAuth-токен сервисного аккаунта; google-auth сам обновляет его по истечении."""
    global _credentials
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with _credentials_lock:
        if _credentials is None:
            _credentials = service_account.Credentials.from_service_account_file(
                settings.FCM_CREDENTIALS_FILE, scopes=[SCOPE],
            )
        if not _credentials.valid:
            _credentials.refresh(Request())
        return _credentials.token


def send_to_token(token, data, collapse_key):
    """
    Одно уведомление. Возвращает False, если токен больше не действует
    (приложение удалено или данные очищены) — тогда его надо забыть.
    """
    import requests

    response = requests.post(
        f'https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send',
        headers={'Authorization': f'Bearer {_access_token()}'},
        json={'message': {
            'token': token,
            'data': {key: str(value) for key, value in data.items()},
            # Высокий приоритет — чтобы уведомление пришло и в режиме
            # энергосбережения; collapse_key схлопывает пачку сообщений
            # одного чата, пока телефон был без сети
            'android': {'priority': 'high', 'collapse_key': collapse_key},
        }},
        timeout=10,
    )
    if response.status_code == 200:
        return True
    gone = 'UNREGISTERED' in response.text or 'registration-token-not-registered' in response.text
    if response.status_code in (400, 404) and gone:
        return False
    logger.warning('FCM ответил %s: %s', response.status_code, response.text[:300])
    return True


def recipients_for(message):
    """
    Кому сообщать: участники чата, кроме отправителя и тех, кто
    отправителя заблокировал (его сообщения они всё равно не увидят).
    """
    members = ChatMember.objects.filter(chat_id=message.chat_id).exclude(user_id=message.sender_id)
    blockers = UserBlock.objects.filter(blocked_id=message.sender_id).values_list('blocker_id', flat=True)
    return list(members.exclude(user_id__in=blockers).values_list('user_id', flat=True))


def _deliver(user_ids, data, collapse_key):
    tokens = list(DeviceToken.objects.filter(user_id__in=user_ids).values_list('token', flat=True))
    stale = []
    for token in tokens:
        try:
            if not send_to_token(token, data, collapse_key):
                stale.append(token)
        except Exception:  # сеть, ключ, Google — уведомление не повод ронять что-либо
            logger.exception('Не удалось отправить push-уведомление')
    if stale:
        DeviceToken.objects.filter(token__in=stale).delete()


def notify_new_message(message):
    if not is_enabled() or message.sender_id is None:
        return
    user_ids = recipients_for(message)
    if not user_ids:
        return
    data = {'type': 'message', 'chat_id': message.chat_id}
    collapse_key = f'chat-{message.chat_id}'
    transaction.on_commit(
        lambda: threading.Thread(
            target=_deliver, args=(user_ids, data, collapse_key), daemon=True,
        ).start()
    )


def register(user, token, platform='android'):
    token = (token or '').strip()
    if not token or len(token) > 255:
        return False
    DeviceToken.objects.update_or_create(token=token, defaults={'user': user, 'platform': platform})
    return True


def unregister(user, token):
    DeviceToken.objects.filter(user=user, token=(token or '').strip()).delete()
