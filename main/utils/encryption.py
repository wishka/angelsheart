from cryptography.fernet import Fernet
from django.conf import settings
import base64
import json

def get_cipher():
    """Получение шифровальщика"""
    key = settings.ENCRYPTION_KEY.encode()
    return Fernet(key)

def encrypt_data(data):
    """Шифрование данных"""
    cipher = get_cipher()
    json_data = json.dumps(data)
    encrypted = cipher.encrypt(json_data.encode())
    return base64.b64encode(encrypted).decode()

def decrypt_data(encrypted_data):
    """Дешифрование данных"""
    cipher = get_cipher()
    decoded = base64.b64decode(encrypted_data.encode())
    decrypted = cipher.decrypt(decoded)
    return json.loads(decrypted.decode())