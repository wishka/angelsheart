from django.db import migrations

# Справочник интересов. Свой список, а не свободный ввод: иначе «кино»
# и «фильмы» становятся разными интересами и поиск по ним не работает.
INTERESTS = [
    ('volunteering', 'Волонтёрство'),
    ('animals', 'Помощь животным'),
    ('health', 'Здоровье'),
    ('education', 'Образование'),
    ('ecology', 'Экология'),
    ('sport', 'Спорт'),
    ('travel', 'Путешествия'),
    ('music', 'Музыка'),
    ('cinema', 'Кино'),
    ('books', 'Книги'),
    ('art', 'Искусство'),
    ('photo', 'Фотография'),
    ('cooking', 'Кулинария'),
    ('it', 'IT и технологии'),
    ('games', 'Игры'),
    ('family', 'Семья и дети'),
]


def seed(apps, schema_editor):
    Interest = apps.get_model('social', 'Interest')
    for position, (slug, title) in enumerate(INTERESTS):
        Interest.objects.update_or_create(slug=slug, defaults={'title': title, 'position': position})


def unseed(apps, schema_editor):
    Interest = apps.get_model('social', 'Interest')
    Interest.objects.filter(slug__in=[slug for slug, _ in INTERESTS]).delete()


class Migration(migrations.Migration):
    dependencies = [('social', '0001_initial')]
    operations = [migrations.RunPython(seed, unseed)]
