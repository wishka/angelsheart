"""
Соблюдение сроков хранения персональных данных.

Сроки были объявлены в Политике («сканы — 180 дней», «журналы — 365 дней»),
но нигде не выполнялись: данные хранились вечно. Обещание в документе,
не подкреплённое кодом, — это недостоверные сведения об обработке
и нарушение п. 7 ч. 1 ст. 5 152-ФЗ («хранение не дольше, чем этого
требуют цели обработки»).

Запускать ежедневно, например через cron:
    0 4 * * *  cd /path/to/project && python manage.py enforce_retention

Флаг --dry-run показывает, что будет удалено, ничего не трогая.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import (ConsentLog, FundraiseDocument, KYCDocument,
                         PersonalDataAccessLog, SecurityLog)


class Command(BaseCommand):
    help = 'Удаляет данные, у которых истёк срок хранения, согласно Политике'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать, что будет удалено, ничего не удаляя',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        retention = settings.DATA_RETENTION
        now = timezone.now()

        if dry_run:
            self.stdout.write(self.style.WARNING('Пробный запуск: ничего не удаляется\n'))

        self._purge_kyc_documents(now, retention['kyc_documents_days'], dry_run)
        self._purge_security_logs(now, retention['security_log_days'], dry_run)
        self._purge_consent_logs(now, retention['financial_years'], dry_run)
        self._purge_pd_access_logs(now, retention['pd_access_log_days'], dry_run)
        self._purge_fundraise_documents(now, retention['fundraise_documents_days'], dry_run)
        self._purge_abandoned_draft_documents(now, retention['abandoned_draft_days'], dry_run)
        self._report_anonymised_accounts(now, retention['account_deletion_days'], dry_run)

    def _purge_kyc_documents(self, now, days, dry_run):
        """
        Сканы документов после завершения проверки.

        Удаляются только проверенные документы: пока заявка на рассмотрении,
        скан нужен. Сама запись остаётся — она подтверждает факт проверки,
        стирается только изображение.
        """
        cutoff = now - timedelta(days=days)
        documents = KYCDocument.objects.filter(
            status__in=['approved', 'rejected'],
            verified_at__lt=cutoff,
        ).exclude(document_image='')

        count = documents.count()
        self.stdout.write(f'Сканы документов старше {days} дней: {count}')

        if count and not dry_run:
            for document in documents:
                document.document_image.delete(save=False)
                document.document_image = ''
                document.save(update_fields=['document_image'])
            self.stdout.write(self.style.SUCCESS(f'  удалено изображений: {count}'))

    def _purge_security_logs(self, now, days, dry_run):
        """Журнал событий безопасности."""
        cutoff = now - timedelta(days=days)
        logs = SecurityLog.objects.filter(created_at__lt=cutoff)

        count = logs.count()
        self.stdout.write(f'Записи журнала безопасности старше {days} дней: {count}')

        if count and not dry_run:
            deleted, _ = logs.delete()
            self.stdout.write(self.style.SUCCESS(f'  удалено записей: {deleted}'))

    def _purge_consent_logs(self, now, years, dry_run):
        """
        Журнал согласий.

        Хранится столько же, сколько сведения об операциях: это
        доказательство правового основания обработки, и удалять его
        раньше финансовых документов нельзя.
        """
        cutoff = now - timedelta(days=365 * years)
        logs = ConsentLog.objects.filter(created_at__lt=cutoff)

        count = logs.count()
        self.stdout.write(f'Записи журнала согласий старше {years} лет: {count}')

        if count and not dry_run:
            deleted, _ = logs.delete()
            self.stdout.write(self.style.SUCCESS(f'  удалено записей: {deleted}'))

    def _purge_pd_access_logs(self, now, days, dry_run):
        """
        Журнал обращений сотрудников к персональным данным.

        Сам журнал тоже содержит ПДн (кто, к чьим данным, с какого адреса),
        поэтому вечно храниться не может. Срок больше, чем у журнала входов:
        расследование инцидента обычно начинается спустя время после него.
        """
        cutoff = now - timedelta(days=days)
        logs = PersonalDataAccessLog.objects.filter(created_at__lt=cutoff)

        count = logs.count()
        self.stdout.write(f'Записи журнала доступа к ПДн старше {days} дней: {count}')

        if count and not dry_run:
            deleted, _ = logs.delete()
            self.stdout.write(self.style.SUCCESS(f'  удалено записей: {deleted}'))

    def _purge_fundraise_documents(self, now, days, dry_run):
        """
        Документы, приложенные к завершённым и отменённым сборам.

        Политика обещает, что они удаляются вместе со сбором. Сбор при этом
        не удаляется — у него меняется статус, — поэтому без этого шага
        медицинские справки лежали бы в хранилище бессрочно, включая справки
        людей, уже удаливших учётную запись.

        Удаляется и файл, и запись: в отличие от скана паспорта, здесь нечего
        оставлять как отметку о проверке — результат проверки хранится
        в самом сборе.
        """
        cutoff = now - timedelta(days=days)
        # Срок считается от закрытия сбора, а не от загрузки документа:
        # именно так обещает Политика, и документ, приложенный год назад
        # к сбору, закрытому вчера, удалять сегодня нельзя.
        documents = FundraiseDocument.objects.filter(
            fundraise__closed_at__lt=cutoff,
        )

        count = documents.count()
        self.stdout.write(f'Документы к сборам, закрытым более {days} дней назад: {count}')

        if count and not dry_run:
            self._delete_documents(documents)
            self.stdout.write(self.style.SUCCESS(f'  удалено документов: {count}'))

    def _purge_abandoned_draft_documents(self, now, days, dry_run):
        """
        Документы к черновикам, брошенным автором.

        Такой сбор не закрыт и под предыдущее правило не подпадает, поэтому
        медицинская справка, приложенная к заявке, которую так и не подали,
        лежала бы вечно. Год без движения — это отказ от заявки.
        """
        cutoff = now - timedelta(days=days)
        documents = FundraiseDocument.objects.filter(
            fundraise__status='draft',
            fundraise__closed_at__isnull=True,
            uploaded_at__lt=cutoff,
        )

        count = documents.count()
        self.stdout.write(f'Документы к брошенным черновикам старше {days} дней: {count}')

        if count and not dry_run:
            self._delete_documents(documents)
            self.stdout.write(self.style.SUCCESS(f'  удалено документов: {count}'))

    def _delete_documents(self, documents):
        for document in documents:
            document.file.delete(save=False)
            document.delete()

    def _report_anonymised_accounts(self, now, days, dry_run):
        """
        Учётные записи, обезличенные по требованию пользователя.

        Срок из настроек объявлялся, но не использовался ничем. Сами записи
        не удаляются и здесь: на них ссылаются транзакции, которые закон
        требует хранить пять лет (п. 4 ст. 7 115-ФЗ), а каскадное удаление
        унесло бы и их. Персональных данных в такой записи уже нет —
        остаётся обезличенный идентификатор, и команда лишь показывает,
        сколько их и с какого времени.
        """
        from django.contrib.auth.models import User

        cutoff = now - timedelta(days=days)
        accounts = User.objects.filter(
            is_active=False, username__startswith='deleted_', date_joined__lt=cutoff,
        )
        self.stdout.write(
            f'Обезличенных учётных записей старше {days} дней: {accounts.count()} '
            f'(сохраняются: на них ссылаются сведения об операциях)'
        )
