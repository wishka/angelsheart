"""
Приём изображений: фото анкеты и вложения в чате.

Каждое изображение открывается Pillow и пересохраняется заново в JPEG.
Это не прихоть: исходный файл с телефона несёт EXIF, а в нём часто
координаты места съёмки и модель телефона. Пересохранение без EXIF
отрезает их, заодно поворачивает снимок по метке ориентации и не
пропускает «картинку», которая на деле — что-то другое.
"""

import io
import uuid

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
AVATAR_SIDE = 512
CHAT_SIDE = 1600
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}


class ImageRejected(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def prepare(uploaded, max_side, square=False):
    """
    Проверенное и пересохранённое изображение для ImageField.

    square=True обрезает до квадрата по центру — для фото анкеты, которое
    показывается в кружке.
    """
    if uploaded is None:
        raise ImageRejected('Файл не передан')
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ImageRejected('Файл больше 8 МБ')

    try:
        image = Image.open(uploaded)
        if image.format not in ALLOWED_FORMATS:
            raise ImageRejected('Поддерживаются JPEG, PNG, WEBP и GIF')
        image.load()
    except Image.DecompressionBombError:
        raise ImageRejected('Слишком большое изображение')
    except (UnidentifiedImageError, OSError):
        raise ImageRejected('Файл не похож на изображение')

    image = ImageOps.exif_transpose(image).convert('RGB')
    if square:
        image = ImageOps.fit(image, (max_side, max_side))
    else:
        image.thumbnail((max_side, max_side))

    buffer = io.BytesIO()
    # exif не передаётся — метаданные исходника не попадают в файл
    image.save(buffer, format='JPEG', quality=85, optimize=True)
    return ContentFile(buffer.getvalue(), name=f'{uuid.uuid4().hex}.jpg')
