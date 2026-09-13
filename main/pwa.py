"""
Установка сайта как приложения: манифест и service worker.

Манифест и service worker отдаются вью, а не лежат статикой, по двум
причинам. Во-первых, service worker обязан отдаваться из корня сайта:
его область действия ограничена каталогом, из которого он получен, и
файл из /static/ управлял бы только /static/. Во-вторых, оба файла
ссылаются на адреса и версии статики, а те меняются при каждом
collectstatic — собирать их шаблоном надёжнее, чем помнить про правку
руками.

Внешних адресов здесь нет: иконки, шрифты и значки раздаются со своего
сервера (см. комментарий в settings про трансграничную передачу).
"""

import hashlib
import os
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods

APP_NAME = 'Ангел-Хранитель'
APP_SHORT_NAME = 'Ангел'
THEME_COLOR = '#d4737a'
BACKGROUND_COLOR = '#fdf4f0'

ICON_SIZES = (48, 72, 96, 144, 192, 256, 384, 512)


@require_GET
def web_manifest(request):
    """Манифест приложения."""
    icons = [
        {
            'src': static(f'icons/icon-{size}.png'),
            'sizes': f'{size}x{size}',
            'type': 'image/png',
            'purpose': 'any',
        }
        for size in ICON_SIZES
    ]
    # Отдельные иконки с purpose=maskable: система обрезает значок под
    # форму, принятую на устройстве, и у обычной иконки при этом
    # срезаются края рисунка
    icons += [
        {
            'src': static(f'icons/icon-maskable-{size}.png'),
            'sizes': f'{size}x{size}',
            'type': 'image/png',
            'purpose': 'maskable',
        }
        for size in (192, 512)
    ]

    return JsonResponse(
        {
            'id': '/',
            'name': APP_NAME,
            'short_name': APP_SHORT_NAME,
            'description': 'Платформа взаимопомощи: переводы, сборы средств, пожертвования',
            'lang': 'ru',
            'dir': 'ltr',
            'start_url': '/',
            'scope': '/',
            # standalone — без адресной строки: приложение, а не вкладка
            'display': 'standalone',
            # Не запираем в портрет: таблицы уровней и история операций
            # шире экрана, и поворот — единственный способ увидеть их
            # целиком. В twa-manifest.json стоит то же самое ('default'),
            # иначе установка из браузера и установка из APK вели бы
            # себя по-разному.
            'orientation': 'any',
            'theme_color': THEME_COLOR,
            'background_color': BACKGROUND_COLOR,
            'categories': ['finance', 'social'],
            'icons': icons,
            # Быстрые действия по долгому нажатию на значок
            'shortcuts': [
                {
                    'name': 'Перевести',
                    'short_name': 'Перевод',
                    'url': reverse('main:transfer'),
                    'icons': [{'src': static('icons/icon-96.png'), 'sizes': '96x96'}],
                },
                {
                    'name': 'Сборы средств',
                    'short_name': 'Сборы',
                    'url': reverse('main:fundraises'),
                    'icons': [{'src': static('icons/icon-96.png'), 'sizes': '96x96'}],
                },
                {
                    'name': 'История операций',
                    'short_name': 'История',
                    'url': reverse('main:history'),
                    'icons': [{'src': static('icons/icon-96.png'), 'sizes': '96x96'}],
                },
            ],
        },
        json_dumps_params={'ensure_ascii': False},
        content_type='application/manifest+json',
    )


# Шаблоны, попадающие в кэш приложения. Их содержимое входит в версию:
# иначе правка офлайн-страницы не меняет ни версию, ни байты самого
# service worker — браузер не видит обновления и навсегда оставляет
# в кэше старую страницу. В DEBUG адреса статики не хэшируются, так что
# без этого версия не менялась бы вообще никогда.
_CACHED_TEMPLATES = ('main/offline.html', 'pwa/service-worker.js')


def _cache_version():
    """
    Версия кэша. Меняется при обновлении статики, шаблонов из кэша или
    редакции документов, чтобы у людей не оставалось старое оформление.
    """
    from django.template.loader import get_template

    parts = [settings.LEGAL_DOCS_VERSION, static('css/mobile.css')]
    for name in _CACHED_TEMPLATES:
        try:
            source = Path(get_template(name).origin.name).read_bytes()
        except (OSError, AttributeError):
            # Шаблон может отдаваться не из файла (кэширующий загрузчик
            # в экзотической сборке). Тогда версия просто не учитывает
            # его содержимое — это хуже, чем отказ отдать service worker.
            continue
        parts.append(hashlib.sha256(source).hexdigest())

    seed = '|'.join(parts)
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]


@require_GET
# Сам service worker не кэшируется: иначе браузер будет неделями
# показывать старую версию и новый список файлов до людей не дойдёт
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
def service_worker(request):
    """Service worker. Отдаётся из корня, иначе его область — /static/."""
    precache = [
        static('css/mobile.css'),
        static('vendor/fonts/fonts.css'),
        static('vendor/fontawesome/css/all.min.css'),
        static('vendor/fontawesome/webfonts/fa-solid-900.woff2'),
        static('icons/icon-192.png'),
        static('icons/icon-512.png'),
    ]
    return render(
        request,
        'pwa/service-worker.js',
        {
            'version': _cache_version(),
            'precache': precache,
            'offline_url': reverse('main:offline'),
        },
        content_type='application/javascript; charset=utf-8',
    )


@require_GET
def offline(request):
    """Страница, которую видно без сети."""
    return render(request, 'main/offline.html')


# ==================== УСТАНОВКА НА ANDROID ====================

@require_GET
def asset_links(request):
    """
    Digital Asset Links — связь сайта и приложения.

    Android открывает сайт внутри приложения без адресной строки только
    тогда, когда сайт подтверждает: приложение с такой подписью — наше.
    Файл обязан лежать по адресу /.well-known/assetlinks.json и отдаваться
    по HTTPS без перенаправлений.

    Отпечаток ключа подписи появляется при сборке; до тех пор отдавать
    нечего, и пустой список лучше выдуманного: с неверным отпечатком
    приложение молча покажет адресную строку, и причину будут искать
    неделю.
    """
    from django.http import Http404

    fingerprints = [
        value.strip()
        for value in getattr(settings, 'ANDROID_SIGNING_FINGERPRINTS', '').split(',')
        if value.strip()
    ]
    if not fingerprints:
        raise Http404(
            'Отпечаток ключа подписи не задан: заполните '
            'ANDROID_SIGNING_FINGERPRINTS после сборки приложения'
        )

    return JsonResponse(
        [
            {
                'relation': ['delegate_permission/common.handle_all_urls'],
                'target': {
                    'namespace': 'android_app',
                    'package_name': settings.ANDROID_PACKAGE_NAME,
                    'sha256_cert_fingerprints': fingerprints,
                },
            }
        ],
        safe=False,
    )


def _apk_path():
    return Path(settings.ANDROID_APK_PATH)


def _apk_info():
    """
    Размер и контрольная сумма сборки, либо None, если файла нет.

    Сумма считается один раз на каждую версию файла: ключ кэша включает
    время изменения и размер, так что подменённая сборка пересчитается
    сама, а на каждое открытие страницы гигабайт не читается.

    Файл берётся один раз и через дескриптор: проверить существование,
    а потом обратиться к файлу по имени — значит однажды получить 500
    ровно в момент, когда сборку заменяют на живом сервере.
    """
    apk = _apk_path()
    try:
        handle = apk.open('rb')
    except OSError:
        return None

    with handle:
        stat = os.fstat(handle.fileno())
        key = f'apk-sha256:{stat.st_mtime_ns}:{stat.st_size}'
        digest = cache.get(key)
        if digest is None:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                hasher.update(chunk)
            digest = hasher.hexdigest()
            cache.set(key, digest, 60 * 60 * 24 * 30)

    return {
        'size_mb': round(stat.st_size / 1024 / 1024, 1),
        'sha256': digest,
    }


@require_GET
def app_page(request):
    """Страница установки приложения."""
    info = _apk_info()

    return render(request, 'main/app.html', {
        'apk_ready': info is not None,
        'apk_size_mb': info['size_mb'] if info else None,
        'apk_sha256': info['sha256'] if info else None,
        'app_version': settings.ANDROID_APP_VERSION,
        'package_name': settings.ANDROID_PACKAGE_NAME,
    })


# HEAD разрешён намеренно: менеджеры загрузок Android и часть браузеров
# начинают скачивание именно с него, а require_GET отвечал бы им 405.
@require_http_methods(['GET', 'HEAD'])
def download_apk(request):
    """
    Отдача файла приложения.

    Файл лежит вне статики: его меняют чаще, чем собирают статику, и
    скачивания видно в журнале.

    В проде отдавать его должен nginx (ANDROID_APK_INTERNAL_LOCATION):
    отдача питоном занимает рабочий процесс на всё время передачи —
    десяток телефонов на мобильной сети кладут сайт, — и не умеет
    докачки, так что обрыв на 90 % означает качать сборку заново.
    """
    from django.http import FileResponse, Http404, HttpResponse

    filename = f'angelsheart-{settings.ANDROID_APP_VERSION}.apk'
    content_type = 'application/vnd.android.package-archive'

    internal = getattr(settings, 'ANDROID_APK_INTERNAL_LOCATION', '')
    if internal:
        # nginx: internal location, отдающий тот же файл. Django только
        # решает, отдавать ли, и сразу освобождает процесс.
        response = HttpResponse(content_type=content_type)
        response['X-Accel-Redirect'] = internal
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    apk = _apk_path()
    try:
        handle = apk.open('rb')
    except OSError:
        raise Http404('Сборка приложения ещё не загружена на сервер')

    response = FileResponse(
        handle,
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )
    # Докачки нет — говорим об этом прямо, чтобы клиент не пытался
    # продолжить с середины и не получал молча файл целиком заново
    response['Accept-Ranges'] = 'none'
    return response
