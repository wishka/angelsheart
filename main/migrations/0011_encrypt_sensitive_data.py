"""
Перенос существующих данных под новую схему.

1. Паспортные данные и номера документов лежали в базе открытым текстом —
   шифруем существующие записи.
2. Реквизиты выплат приводим к зашифрованному виду и заполняем маскированное
   представление для списков.
3. Проставляем Transaction.kind: до этой миграции пополнение записывалось
   как перевод самому себе и неотличимо смешивалось с настоящими переводами,
   задваивая обороты в рейтинге и статистике.
4. Переносим сканы документов в приватное хранилище вне MEDIA_ROOT.
"""

import shutil
from pathlib import Path

from django.conf import settings
from django.db import migrations


def encrypt_existing(apps, schema_editor):
    UserVerification = apps.get_model('main', 'UserVerification')
    KYCDocument = apps.get_model('main', 'KYCDocument')
    WithdrawalRequest = apps.get_model('main', 'WithdrawalRequest')

    # Чтение вернёт открытый текст как есть (значения без префикса enc:v1:),
    # а сохранение прогонит его через шифрование.
    for verification in UserVerification.objects.all().iterator():
        verification.save(update_fields=['passport_series', 'passport_number', 'address'])

    for document in KYCDocument.objects.all().iterator():
        document.save(update_fields=['document_number'])

    from main.utils.encryption import mask_card_number, mask_phone

    for withdrawal in WithdrawalRequest.objects.all().iterator():
        details = withdrawal.payment_details or {}
        if withdrawal.payment_method == 'card':
            masked = mask_card_number(details.get('card_number'))
        elif withdrawal.payment_method == 'sbp':
            masked = mask_phone(details.get('phone_number'))
        else:
            wallet = str(details.get('wallet_number') or '')
            masked = '***' + wallet[-4:] if len(wallet) >= 4 else '***'
        withdrawal.payment_details_masked = masked
        withdrawal.save(update_fields=['payment_details', 'payment_details_masked'])


def backfill_transaction_kind(apps, schema_editor):
    from django.db.models import F

    Transaction = apps.get_model('main', 'Transaction')

    Transaction.objects.filter(is_donation=True).update(kind='donation')
    # Перевод самому себе мог означать только пополнение баланса
    Transaction.objects.filter(is_donation=False, sender_id=F('receiver_id')).update(kind='topup')


def move_kyc_files(apps, schema_editor):
    """Перенос сканов из media/kyc/ в приватный каталог."""
    source_root = Path(settings.MEDIA_ROOT) / 'kyc'
    target_root = Path(settings.PRIVATE_MEDIA_ROOT) / 'kyc'

    if not source_root.exists():
        return

    target_root.parent.mkdir(parents=True, exist_ok=True)
    for source in source_root.rglob('*'):
        if not source.is_file():
            continue
        target = target_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)


def noop(apps, schema_editor):
    """Обратная миграция не расшифровывает данные намеренно."""


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0010_remove_cryptobalance_user_and_more'),
    ]

    operations = [
        migrations.RunPython(encrypt_existing, noop),
        migrations.RunPython(backfill_transaction_kind, noop),
        migrations.RunPython(move_kyc_files, noop),
    ]
