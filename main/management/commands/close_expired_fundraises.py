"""
Закрытие сборов, у которых истёк срок.

Поле end_date заполнялось автором и показывалось на странице сбора
(«До: 31.12.2026»), но не проверялось нигде: сбор с истёкшим сроком
оставался на витрине и продолжал принимать пожертвования. Для
жертвователя это прямой обман — он видит срок и считает его условием
сбора.

Сбор именно завершается, а не отменяется: срок вышел, но собранное
принадлежит автору, и возвращать деньги не за что. Отмена с возвратом —
отдельное решение автора или модератора.

Ставится в cron раз в час:
    0 * * * *  cd /path/to/project && python manage.py close_expired_fundraises
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import Fundraise


class Command(BaseCommand):
    help = 'Завершает сборы, у которых прошла дата окончания'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать, что будет закрыто, ничего не меняя',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        expired = Fundraise.objects.filter(
            status='active', end_date__isnull=False, end_date__lt=now,
        ).select_related('author')

        count = expired.count()
        self.stdout.write(f'Сборов с истёкшим сроком: {count}')

        if not count:
            return

        for fundraise in expired:
            self.stdout.write(
                f'  #{fundraise.pk} «{fundraise.title}» '
                f'({fundraise.author.username}, срок {fundraise.end_date:%d.%m.%Y})'
            )
            if not options['dry_run']:
                # save() через модель, а не queryset.update(): нужен
                # closed_at, от которого считается срок хранения документов
                fundraise.status = 'completed'
                fundraise.save(update_fields=['status'])

        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(f'Завершено сборов: {count}'))
