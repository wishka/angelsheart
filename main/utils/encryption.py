"""
Шифрование чувствительных данных (паспорта, платёжные реквизиты).

Ключ берётся из settings.ENCRYPTION_KEY — он валидируется при старте
проекта, поэтому здесь дополнительных проверок не нужно.
"""

import base64
import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Префикс, по которому отличаем зашифрованное значение от унаследованного
# открытого текста. Нужен, пока в базе есть записи, созданные до шифрования.
PREFIX = 'enc:v1:'


@lru_cache(maxsize=1)
def get_cipher():
    """Получение шифровальщика (кешируется: разбор ключа не бесплатный)."""
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_str(value):
    """Шифрование строки. Уже зашифрованное значение возвращается как есть."""
    if value is None or value == '':
        return value
    value = str(value)
    if value.startswith(PREFIX):
        return value
    token = get_cipher().encrypt(value.encode())
    return PREFIX + token.decode()


def decrypt_str(value):
    """
    Расшифровка строки.

    Значение без префикса считается унаследованным открытым текстом и
    возвращается как есть — иначе переход на шифрование сломал бы чтение
    старых записей. После прогона data-миграции таких значений быть не должно.
    """
    if value is None or value == '':
        return value
    value = str(value)
    if not value.startswith(PREFIX):
        return value
    try:
        return get_cipher().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        # Ключ сменили, а данные не перешифровали. Молча отдавать мусор нельзя.
        raise ValueError(
            'Не удалось расшифровать значение: ENCRYPTION_KEY не совпадает '
            'с тем, которым данные были зашифрованы.'
        )


def encrypt_data(data):
    """Шифрование произвольной JSON-сериализуемой структуры."""
    cipher = get_cipher()
    encrypted = cipher.encrypt(json.dumps(data, ensure_ascii=False).encode())
    return base64.b64encode(encrypted).decode()


def decrypt_data(encrypted_data):
    """Расшифровка структуры, зашифрованной encrypt_data."""
    cipher = get_cipher()
    decoded = base64.b64decode(encrypted_data.encode())
    return json.loads(cipher.decrypt(decoded).decode())


def mask_card_number(card_number):
    """Маскирование номера карты до вида ****1234 для показа и логов."""
    digits = ''.join(ch for ch in str(card_number or '') if ch.isdigit())
    if len(digits) < 4:
        return '****'
    return '****' + digits[-4:]


def mask_phone(phone):
    """Маскирование номера телефона."""
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if len(digits) < 7:
        return '***'
    return f'{digits[:2]}***{digits[-4:]}'
