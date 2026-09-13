"""
Извлечение IP-адреса и User-Agent из запроса.

Раньше код брал первый адрес из X-Forwarded-For. Этот заголовок целиком
подконтролен клиенту: любой мог прислать `X-Forwarded-For: 1.2.3.4` и
записать в журнал согласий и в SecurityLog чужой адрес, а заодно обойти
счётчик попыток входа, меняя заголовок на каждом запросе.

Доверять можно только тем адресам, которые дописали наши собственные
прокси. Их количество задаётся settings.TRUSTED_PROXY_COUNT.
"""

from django.conf import settings


def get_client_ip(request):
    """IP клиента с учётом количества доверенных прокси перед приложением."""
    trusted = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)
    remote_addr = request.META.get('REMOTE_ADDR')

    if not trusted:
        # Прокси нет — единственный достоверный источник это сокет.
        return remote_addr

    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    chain = [part.strip() for part in forwarded.split(',') if part.strip()]
    if not chain:
        return remote_addr

    # Последний элемент дописан ближайшим прокси, предпоследний — следующим
    # за ним и так далее. Всё, что левее наших прокси, подделано клиентом.
    index = len(chain) - trusted
    if index < 0:
        return remote_addr
    return chain[index]


def get_user_agent(request):
    """User-Agent, обрезанный до разумной длины."""
    return request.META.get('HTTP_USER_AGENT', '')[:512]


def get_request_meta(request):
    """Пара (ip, user_agent) — то, что пишется в журналы согласий и безопасности."""
    return get_client_ip(request), get_user_agent(request)
