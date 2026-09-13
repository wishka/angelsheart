"""
Проверка подлинности уведомлений от платёжных систем.

До этого модуля вью `payment_webhook` зачисляла деньги по любому POST-запросу:
подписи не было, IP не проверялся, сумма бралась из тела запроса. Обычным
curl'ом можно было начислить себе произвольную сумму.

ЮKassa не подписывает уведомления. Её штатная схема доверия — два шага:
  1. запрос пришёл с их IP-диапазона;
  2. платёж перезапрашивается через API, и решение принимается по ответу API,
     а не по телу уведомления.
Второй шаг главный: даже если IP подделан или диапазоны изменились,
деньги зачисляются только если сам ЮKassa подтвердил оплату и сумму.
"""

import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger('security')


class WebhookRejected(Exception):
    """Уведомление не прошло проверку подлинности."""


def is_trusted_ip(ip_address):
    """Входит ли адрес в разрешённые диапазоны ЮKassa."""
    if not ip_address:
        return False
    try:
        addr = ipaddress.ip_address(ip_address)
    except ValueError:
        return False

    for entry in settings.YOOKASSA_WEBHOOK_IPS:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning('Некорректный диапазон в YOOKASSA_WEBHOOK_IPS: %s', entry)
    return False


def verify_yookassa_notification(request, payment_id):
    """
    Подтверждает оплату у ЮKassa и возвращает данные платежа.

    Возвращает dict со статусом и суммой, полученными от API, либо
    поднимает WebhookRejected. Тело уведомления используется только для
    того, чтобы узнать, какой платёж перепроверить.
    """
    from main.utils.request_meta import get_client_ip

    client_ip = get_client_ip(request)
    if not is_trusted_ip(client_ip):
        logger.warning(
            'Уведомление ЮKassa с недоверенного адреса %s (платёж %s)',
            client_ip, payment_id,
        )
        raise WebhookRejected('Адрес отправителя не входит в диапазоны ЮKassa')

    if not payment_id:
        raise WebhookRejected('В уведомлении нет идентификатора платежа')

    from .yookassa import YooKassaProvider

    result = YooKassaProvider().check_payment(payment_id)
    if not result.get('success'):
        logger.warning(
            'Не удалось подтвердить платёж %s через API ЮKassa: %s',
            payment_id, result.get('error'),
        )
        raise WebhookRejected('Платёж не подтверждён платёжной системой')

    return result
