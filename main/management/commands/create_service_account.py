"""
Создание служебного аккаунта для учёта комиссии сервиса.

Комиссия — это деньги, которые кто-то должен получить. Без такого аккаунта
удержанная сумма просто исчезала бы из системы: донор списан на полную,
автор получил меньше, разница в никуда.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from main.models import Balance


class Command(BaseCommand):
    help = 'Создаёт служебный аккаунт, на который зачисляется комиссия сервиса'

    def handle(self, *args, **options):
        from django.conf import settings

        username = settings.SERVICE_ACCOUNT_USERNAME
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': '',
                'first_name': 'Служебный',
                'last_name': 'аккаунт',
                'is_active': False,  # войти под ним нельзя
            },
        )
        if created:
            # Пароль не задаётся: set_unusable_password делает вход невозможным
            user.set_unusable_password()
            user.save(update_fields=['password'])

        Balance.objects.get_or_create(user=user)

        if created:
            self.stdout.write(self.style.SUCCESS(f'Служебный аккаунт «{username}» создан.'))
        else:
            self.stdout.write(f'Служебный аккаунт «{username}» уже существует.')

        balance = Balance.objects.get(user=user)
        self.stdout.write(f'Накоплено комиссии: {balance.amount} ₽')
