from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import IntegrityError
from decimal import Decimal
from main.models import Balance, Fundraise, Donation
import random
from datetime import datetime, timedelta


class Command(BaseCommand):
    help = 'Создает тестовых пользователей для тестирования платформы'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=12,
            help='Количество пользователей для создания (по умолчанию: 12)'
        )
        parser.add_argument(
            '--with-balance',
            action='store_true',
            help='Добавить баланс пользователям'
        )
        parser.add_argument(
            '--with-fundraises',
            action='store_true',
            help='Создать тестовые сборы'
        ),
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Очистить существующих тестовых пользователей перед созданием'
        )
    
    def handle(self, *args, **options):
        count = options['count']
        with_balance = options['with_balance']
        with_fundraises = options['with_fundraises']
        clear_first = options['clear']
        
        # Список тестовых пользователей
        test_users_data = [
            {'username': 'alexey_medvedev', 'email': 'alexey@example.com',
             'first_name': 'Алексей', 'last_name': 'Медведев', 'bio': 'Помогаю животным'},
            {'username': 'elena_smirnova', 'email': 'elena@example.com',
             'first_name': 'Елена', 'last_name': 'Смирнова', 'bio': 'Поддерживаю образование'},
            {'username': 'dmitry_volkov', 'email': 'dmitry@example.com',
             'first_name': 'Дмитрий', 'last_name': 'Волков', 'bio': 'Экоактивист'},
            {'username': 'anna_kuznetsova', 'email': 'anna@example.com',
             'first_name': 'Анна', 'last_name': 'Кузнецова', 'bio': 'Волонтер'},
            {'username': 'sergey_mikhailov', 'email': 'sergey@example.com',
             'first_name': 'Сергей', 'last_name': 'Михайлов', 'bio': 'Спортсмен'},
            {'username': 'olga_novikova', 'email': 'olga@example.com',
             'first_name': 'Ольга', 'last_name': 'Новикова', 'bio': 'Благотворитель'},
            {'username': 'andrey_fedorov', 'email': 'andrey@example.com',
             'first_name': 'Андрей', 'last_name': 'Федоров', 'bio': 'Предприниматель'},
            {'username': 'maria_pavlova', 'email': 'maria@example.com',
             'first_name': 'Мария', 'last_name': 'Павлова', 'bio': 'Врач'},
            {'username': 'ivan_sokolov', 'email': 'ivan@example.com',
             'first_name': 'Иван', 'last_name': 'Соколов', 'bio': 'Студент'},
            {'username': 'tatyana_vasilieva', 'email': 'tatyana@example.com',
             'first_name': 'Татьяна', 'last_name': 'Васильева', 'bio': 'Учитель'},
            {'username': 'maksim_romanov', 'email': 'maksim@example.com',
             'first_name': 'Максим', 'last_name': 'Романов', 'bio': 'IT-специалист'},
            {'username': 'nadezhda_medvedeva', 'email': 'nadezhda@example.com',
             'first_name': 'Надежда', 'last_name': 'Медведева', 'bio': 'Психолог'},
            {'username': 'viktor_orlov', 'email': 'viktor@example.com',
             'first_name': 'Виктор', 'last_name': 'Орлов', 'bio': 'Фотограф'},
            {'username': 'ekaterina_belova', 'email': 'ekaterina@example.com',
             'first_name': 'Екатерина', 'last_name': 'Белова', 'bio': 'Дизайнер'},
            {'username': 'pavel_nikitin', 'email': 'pavel@example.com',
             'first_name': 'Павел', 'last_name': 'Никитин', 'bio': 'Музыкант'},
        ]
        
        # Ограничиваем количество
        users_to_create = test_users_data[:count]
        
        self.stdout.write(self.style.SUCCESS('🚀 Начинаем создание тестовых пользователей...'))
        self.stdout.write(f'📊 Будет создано: {len(users_to_create)} пользователей')
        
        # Очистка существующих
        if clear_first:
            self.stdout.write(self.style.WARNING('🗑️ Очистка существующих тестовых пользователей...'))
            for user_data in users_to_create:
                User.objects.filter(username=user_data['username']).delete()
        
        created_users = []
        balance_added = 0
        
        for user_data in users_to_create:
            try:
                # Проверяем существование
                if User.objects.filter(username=user_data['username']).exists():
                    self.stdout.write(self.style.WARNING(f'⏩ Пользователь {user_data["username"]} уже существует'))
                    user = User.objects.get(username=user_data['username'])
                else:
                    # Создаем пользователя
                    user = User.objects.create_user(
                        username=user_data['username'],
                        email=user_data['email'],
                        password='test123456',
                        first_name=user_data['first_name'],
                        last_name=user_data['last_name']
                    )
                    created_users.append(user)
                    self.stdout.write(
                        self.style.SUCCESS(f'✅ Создан: {user.username} - {user.first_name} {user.last_name}'))
                
                # Добавляем баланс
                if with_balance:
                    balance, created = Balance.objects.get_or_create(user=user)
                    if created or balance.amount == 0:
                        # Случайный баланс от 1000 до 50000
                        random_balance = Decimal(random.randint(1000, 50000))
                        balance.amount = random_balance
                        balance.save()
                        balance_added += 1
                        self.stdout.write(f'   💰 Баланс: {balance.amount} ₽')
            
            except IntegrityError as e:
                self.stdout.write(self.style.ERROR(f'❌ Ошибка при создании {user_data["username"]}: {e}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'❌ Непредвиденная ошибка: {e}'))
        
        # Создаем тестовые сборы
        if with_fundraises and created_users:
            self.stdout.write(self.style.WARNING('\n📦 Создание тестовых сборов...'))
            test_fundraises = [
                {'title': 'Помощь бездомным животным', 'description': 'Сбор на корм и лечение бездомных кошек и собак',
                 'category': 'animal', 'target_amount': 100000},
                {'title': 'Спортивная секция для детей',
                 'description': 'Открытие бесплатной секции по футболу для детей из малообеспеченных семей',
                 'category': 'sport', 'target_amount': 50000},
                {'title': 'Экологический проект "Чистый берег"',
                 'description': 'Очистка береговой линии и посадка деревьев',
                 'category': 'ecology', 'target_amount': 30000},
                {'title': 'Помощь сельской школе', 'description': 'Закупка компьютеров и учебников для сельской школы',
                 'category': 'education', 'target_amount': 75000},
                {'title': 'Поддержка молодых художников',
                 'description': 'Организация выставки и покупка материалов для молодых талантов',
                 'category': 'art', 'target_amount': 40000},
            ]
            
            fundraises_created = 0
            for i, fund_data in enumerate(test_fundraises):
                author = created_users[i % len(created_users)]
                fundraise = Fundraise.objects.create(
                    title=fund_data['title'],
                    description=fund_data['description'],
                    category=fund_data['category'],
                    target_amount=fund_data['target_amount'],
                    author=author,
                    status='active'
                )
                fundraises_created += 1
                self.stdout.write(f'   📌 Создан сбор: {fundraise.title} (автор: {author.username})')
                
                # Добавляем несколько тестовых пожертвований
                for j in range(random.randint(1, 5)):
                    donor = created_users[random.randint(0, len(created_users) - 1)]
                    if donor != author:
                        amount = Decimal(random.randint(100, 2000))
                        Donation.objects.create(
                            donor=donor,
                            fundraise=fundraise,
                            amount=amount,
                            message=f'Тестовое пожертвование #{j + 1}',
                            is_anonymous=random.choice([True, False])
                        )
                        fundraise.current_amount += amount
                        fundraise.donors_count += 1
                fundraise.save()
                self.stdout.write(f'      🎯 Собрано: {fundraise.current_amount} / {fundraise.target_amount} ₽')
        
        # Вывод статистики
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 50))
        self.stdout.write(self.style.SUCCESS('🎉 ОПЕРАЦИЯ ЗАВЕРШЕНА!'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'✅ Создано новых пользователей: {len(created_users)}')
        self.stdout.write(f'💰 Добавлен баланс: {balance_added} пользователям')
        if with_fundraises:
            self.stdout.write(f'📦 Создано сборов: {fundraises_created}')
        
        # Список для входа
        if created_users:
            self.stdout.write(self.style.SUCCESS('\n📋 ТЕСТОВЫЕ ДАННЫЕ ДЛЯ ВХОДА:'))
            self.stdout.write('=' * 40)
            for user in created_users[:5]:  # Показываем первых 5
                self.stdout.write(f'   👤 {user.username} | Пароль: test123456')
            if len(created_users) > 5:
                self.stdout.write(f'   ... и еще {len(created_users) - 5} пользователей')
            self.stdout.write('=' * 40)
        
        # Подсказки
        self.stdout.write(self.style.WARNING('\n💡 Подсказки:'))
        self.stdout.write('   • Для поиска пользователей введите первые 3 буквы имени в поле "Кому помочь?"')
        self.stdout.write('   • Тестовый пароль для всех пользователей: test123456')
        if with_fundraises:
            self.stdout.write('   • Созданы тестовые сборы, можете попробовать сделать пожертвования')