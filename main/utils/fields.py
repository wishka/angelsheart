"""
Поля модели, хранящие значение в базе в зашифрованном виде.

Важное следствие: Fernet недетерминирован, поэтому по таким полям
невозможен поиск и фильтрация на стороне СУБД. Все `search_fields`
и `filter(...)` по ним убраны — они бы молча возвращали пустой результат.
"""

from django.db import models

from .encryption import decrypt_str, encrypt_str


class EncryptedTextField(models.TextField):
    """TextField, прозрачно шифрующий значение при записи в базу."""

    def from_db_value(self, value, expression, connection):
        return decrypt_str(value)

    def to_python(self, value):
        return decrypt_str(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        return encrypt_str(super().get_prep_value(value))


class EncryptedJSONField(models.TextField):
    """
    Словарь, хранящийся в базе зашифрованной строкой.

    Замена JSONField для платёжных реквизитов: номер карты в открытом
    JSONField — это хранение PAN в нарушение PCI DSS.
    """

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return {}
        import json
        decrypted = decrypt_str(value)
        try:
            return json.loads(decrypted)
        except (ValueError, TypeError):
            return {}

    def to_python(self, value):
        if isinstance(value, dict):
            return value
        return self.from_db_value(value, None, None)

    def get_prep_value(self, value):
        if value in (None, ''):
            return ''
        import json
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return encrypt_str(value)
