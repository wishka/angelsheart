"""
Обновление шрифтов и значков, раздаваемых со своего сервера.

Файлы уже лежат в main/static/vendor/ и в репозитории — команда нужна не
для первого запуска, а чтобы обновить версии, не возвращая внешние ссылки
в шаблоны.

Почему это важно: пока страницы грузили Google Fonts и Font Awesome
с внешних CDN, каждый показ любой страницы — включая саму Политику
конфиденциальности — отправлял IP-адрес и User-Agent посетителя в США.
Юридически это трансграничная передача персональных данных (ст. 12 152-ФЗ),
которая требует отдельного уведомления Роскомнадзора до её начала.

Источник — реестр npm, а не сами CDN: пакеты @fontsource/* и
@fortawesome/fontawesome-free содержат ровно те же файлы, распространяются
по тем же лицензиям (SIL OFL 1.1 для шрифтов, CC BY 4.0 и MIT для Font
Awesome) и доступны там, где cdnjs.cloudflare.com закрыт исходящим фильтром.

    python manage.py vendor_static
    python manage.py collectstatic
"""

import io
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REGISTRY = 'https://registry.npmjs.org'

FONTAWESOME_PACKAGE = '@fortawesome/fontawesome-free'
# Версия зафиксирована: в шаблонах 300+ значков с именами классов шестой
# версии, а седьмая часть имён переименовала. Переход на новую мажорную
# версию — это правка шаблонов, а не смена числа здесь.
FONTAWESOME_VERSION = '6.7.2'
FONTAWESOME_FONTS = ['fa-solid-900', 'fa-regular-400', 'fa-brands-400', 'fa-v4compatibility']

# Наборы символов и начертания — только те, что нужны сайту.
# У Quicksand кириллицы нет вовсе: русский текст показывается запасным
# шрифтом, и скачивать ради него лишние файлы незачем.
FONT_PLAN = [
    ('@fontsource/quicksand', ['latin', 'latin-ext'], ['300', '400', '500', '600', '700']),
    ('@fontsource/playfair-display', ['latin', 'latin-ext', 'cyrillic'],
     ['400', '500', '600', '700']),
]

CSS_HEADER = """/*
 * Шрифты Quicksand и Playfair Display, раздаются с этого же сервера.
 *
 * Раньше они подключались с внешнего CDN, и каждый показ любой страницы —
 * включая саму Политику конфиденциальности — отправлял IP-адрес и
 * User-Agent посетителя за рубеж. Это трансграничная передача
 * персональных данных (ст. 12 152-ФЗ).
 *
 * Файл собран командой `python manage.py vendor_static` из пакетов
 * @fontsource/* (лицензия SIL OFL 1.1, текст лежит рядом).
 *
 * Подключены только нужные сайту наборы символов. У Quicksand кириллицы
 * нет: русский текст показывается запасным шрифтом.
 */
"""


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_package(name, version=None):
    """Скачивает пакет из реестра npm и возвращает открытый tar-архив."""
    meta = json.loads(fetch(f'{REGISTRY}/{name}').decode('utf-8'))
    version = version or meta['dist-tags']['latest']
    if version not in meta['versions']:
        raise CommandError(f'{name}: версия {version} в реестре не найдена')
    tarball = meta['versions'][version]['dist']['tarball']
    return version, tarfile.open(fileobj=io.BytesIO(fetch(tarball)), mode='r:gz')


def extract(archive, member_suffix, destination):
    """Достаёт из архива файл, путь которого кончается на member_suffix."""
    for member in archive.getmembers():
        if member.name.endswith(member_suffix):
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, open(destination, 'wb') as target:
                shutil.copyfileobj(source, target)
            return True
    return False


class Command(BaseCommand):
    help = 'Обновляет шрифты и значки в main/static/vendor/ (источник — реестр npm)'

    def handle(self, *args, **options):
        vendor = Path(settings.BASE_DIR) / 'main' / 'static' / 'vendor'
        self._vendor_fontawesome(vendor / 'fontawesome')
        self._vendor_fonts(vendor / 'fonts')
        self.stdout.write(self.style.SUCCESS(
            '\nГотово. Выполните python manage.py collectstatic.\n'
            'Внешних ссылок в шаблонах быть не должно — это проверяет '
            'тест ExternalResourcesTests.'
        ))

    def _vendor_fontawesome(self, target):
        self.stdout.write(f'Font Awesome {FONTAWESOME_VERSION}...')
        _version, archive = fetch_package(FONTAWESOME_PACKAGE, FONTAWESOME_VERSION)
        with archive:
            css_path = target / 'css' / 'all.min.css'
            if not extract(archive, 'css/all.min.css', css_path):
                raise CommandError('В пакете Font Awesome не найден css/all.min.css')

            # Ссылки на .ttf убираются: этих файлов мы не раздаём, а
            # ManifestStaticFilesStorage требует, чтобы каждый упомянутый
            # в CSS файл существовал, и роняет collectstatic.
            css = css_path.read_text(encoding='utf-8')
            css = re.sub(r',\s*url\([^)]*?\.ttf\)\s*format\("truetype"\)', '', css)
            css = re.sub(r",\s*url\([^)]*?\.ttf\)\s*format\('truetype'\)", '', css)
            css_path.write_text(css, encoding='utf-8')
            extract(archive, 'LICENSE.txt', target / 'LICENSE.txt')
            for name in FONTAWESOME_FONTS:
                # Только woff2: его понимают все браузеры, которые вообще
                # откроют этот сайт, а ttf удваивает объём
                if extract(archive, f'webfonts/{name}.woff2',
                           target / 'webfonts' / f'{name}.woff2'):
                    self.stdout.write(f'  {name}.woff2')

    def _vendor_fonts(self, target):
        target.mkdir(parents=True, exist_ok=True)
        chunks = []

        for package, subsets, weights in FONT_PLAN:
            self.stdout.write(f'{package}...')
            _version, archive = fetch_package(package)
            with archive:
                license_name = f'LICENSE-{package.split("/")[-1]}.txt'
                extract(archive, '/LICENSE', target / license_name)

                for subset in subsets:
                    for weight in weights:
                        css = self._read(archive, f'/{subset}-{weight}.css')
                        if css is None:
                            continue
                        # woff оставлять незачем — он вдвое больше woff2
                        css = re.sub(
                            r",\s*url\(\./files/[^)]+\.woff\)\s*format\('woff'\)", '', css,
                        )
                        for filename in re.findall(r'\./files/([\w.-]+\.woff2)', css):
                            if not (target / filename).exists():
                                extract(archive, f'/files/{filename}', target / filename)
                                self.stdout.write(f'  {filename}')
                        chunks.append(css.replace('./files/', '').strip())

        if not chunks:
            raise CommandError('Ни одного файла шрифтов не получено — CSS не перезаписан')

        (target / 'fonts.css').write_text(
            CSS_HEADER + '\n' + '\n\n'.join(chunks) + '\n', encoding='utf-8',
        )

    @staticmethod
    def _read(archive, suffix):
        for member in archive.getmembers():
            if member.name.endswith(suffix):
                with archive.extractfile(member) as handle:
                    return handle.read().decode('utf-8')
        return None
