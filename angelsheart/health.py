"""
Проверка живости для выкладки: /health/.

Скрипт выкладки (deploy/deploy.sh) после переключения на новый релиз
опрашивает этот адрес и сверяет поле revision с тем, что выкладывал.
Совпало — релиз жив; нет или база недоступна — откат на предыдущий.

Без авторизации и без сведений о пользователях: только «работаю ли я
и какой я версии». Номер ревизии — не секрет, он и так в публичном
репозитории.
"""

from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

REVISION_FILE = Path(settings.BASE_DIR) / 'REVISION'


def revision():
    try:
        return REVISION_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return 'dev'


@never_cache
def health(request):
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status': 'error', 'database': 'unavailable', 'revision': revision()},
                            status=503)
    return JsonResponse({'status': 'ok', 'revision': revision()})
