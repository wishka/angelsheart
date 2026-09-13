"""
Скачивание шрифтов и иконок для раздачи со своего сервера.

Пока страницы грузят Google Fonts и Font Awesome с внешних CDN, каждый
показ любой страницы отправляет IP-адрес и User-Agent посетителя в США.
Это трансграничная передача персональных данных (ст. 12 152-ФЗ): она
требует отдельного уведомления Роскомнадзора до начала передачи, и она
прямо противоречит тексту Политики обработки персональных данных.

Команда скачивает файлы в main/static/vendor/, после чего в .env можно
оставить USE_EXTERNAL_CDN=False и внешних запросов не останется.
"""

import re
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

FONTAWESOME_VERSION = '6.5.1'
FONTAWESOME_CSS = (
    f'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FONTAWESOME_VERSION}/css/all.min.css'
)
FONTAWESOME_FONTS = ['fa-solid-900', 'fa-regular-400', 'fa-brands-400']

GOOGLE_FONTS_CSS = (
    'https://fonts.googleapis.com/css2'
    '?family=Playfair+Display:wght@400;500;600;700'
    '&family=Quicksand:wght@300;400;500;600;700&display=swap'
)
# Без User-Agent Google отдаёт вариант со шрифтами в устаревших форматах
MODERN_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
)


def fetch(url, user_agent=None):
    request = urllib.request.Request(url)
    if user_agent:
        request.add_header('User-Agent', user_agent)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


class Command(BaseCommand):
    help = 'Скачивает шрифты и иконки в main/static/vendor/ для раздачи со своего сервера'

    def handle(self, *args, **options):
        vendor = Path(settings.BASE_DIR) / 'main' / 'static' / 'vendor'

        self._vendor_fontawesome(vendor / 'fontawesome')
        self._vendor_google_fonts(vendor / 'fonts')

        self.stdout.write(self.style.SUCCESS(
            '\nГотово. Теперь укажите в .env: USE_EXTERNAL_CDN=False\n'
            'и выполните python manage.py collectstatic'
        ))

    def _vendor_fontawesome(self, target):
        (target / 'css').mkdir(parents=True, exist_ok=True)
        (target / 'webfonts').mkdir(parents=True, exist_ok=True)

        self.stdout.write('Скачиваю Font Awesome...')
        css = fetch(FONTAWESOME_CSS).decode('utf-8')
        # В CDN-версии пути относительные (../webfonts/) — структура каталогов
        # у нас такая же, поэтому править ссылки не нужно
        (target / 'css' / 'all.min.css').write_text(css, encoding='utf-8')

        for name in FONTAWESOME_FONTS:
            url = (
                f'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/'
                f'{FONTAWESOME_VERSION}/webfonts/{name}.woff2'
            )
            (target / 'webfonts' / f'{name}.woff2').write_bytes(fetch(url))
            self.stdout.write(f'  {name}.woff2')

    def _vendor_google_fonts(self, target):
        target.mkdir(parents=True, exist_ok=True)

        self.stdout.write('Скачиваю Google Fonts...')
        css = fetch(GOOGLE_FONTS_CSS, user_agent=MODERN_UA).decode('utf-8')

        # Заменяем ссылки на fonts.gstatic.com локальными файлами
        for index, url in enumerate(set(re.findall(r'url\((https://[^)]+)\)', css))):
            filename = f'font-{index}.woff2'
            (target / filename).write_bytes(fetch(url))
            css = css.replace(url, filename)
            self.stdout.write(f'  {filename}')

        (target / 'fonts.css').write_text(css, encoding='utf-8')
