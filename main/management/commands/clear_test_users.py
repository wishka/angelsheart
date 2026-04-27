from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Удаляет всех тестовых пользователей'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Подтверждение удаления'
        )
    
    def handle(self, *args, **options):
        if not options['confirm']:
            self.stdout.write(self.style.WARNING('⚠️ ВНИМАНИЕ! Это действие удалит всех тестовых пользователей.'))
            self.stdout.write(self.style.WARNING('Для подтверждения используйте флаг --confirm'))
            return
        
        test_usernames = [
            'alexey_medvedev', 'elena_smirnova', 'dmitry_volkov',
            'anna_kuznetsova', 'sergey_mikhailov', 'olga_novikova',
            'andrey_fedorov', 'maria_pavlova', 'ivan_sokolov',
            'tatyana_vasilieva', 'maksim_romanov', 'nadezhda_medvedeva',
            'viktor_orlov', 'ekaterina_belova', 'pavel_nikitin'
        ]
        
        deleted = 0
        for username in test_usernames:
            user = User.objects.filter(username=username)
            if user.exists():
                user.delete()
                deleted += 1
                self.stdout.write(f'🗑️ Удален: {username}')
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ Удалено пользователей: {deleted}'))