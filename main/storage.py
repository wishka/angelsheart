"""
Приватное хранилище для сканов документов KYC.

Файлы лежат вне MEDIA_ROOT, поэтому веб-сервер не отдаёт их по прямой
ссылке. Раньше паспорта складывались в media/kyc/ — при типовой настройке
nginx («location /media/ { alias ...; }») они оказались бы доступны любому,
кто знает или подберёт путь.

Выдача только через вью `main:kyc_document`, которая проверяет, что файл
запрашивает либо его владелец, либо сотрудник.
"""

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """Хранилище без публичного URL."""

    def __init__(self, **kwargs):
        kwargs.setdefault('location', str(settings.PRIVATE_MEDIA_ROOT))
        # base_url=None: попытка обратиться к .url упадёт явно,
        # а не отдаст работающую публичную ссылку.
        kwargs.setdefault('base_url', None)
        super().__init__(**kwargs)

    def __eq__(self, other):
        return isinstance(other, PrivateMediaStorage)

    def __hash__(self):
        return hash(self.__class__)


private_media_storage = PrivateMediaStorage()
