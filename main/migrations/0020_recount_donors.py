"""
Пересчёт счётчика жертвователей по людям.

Раньше donors_count увеличивался на каждое пожертвование, поэтому один
человек, пожертвовавший дважды, показывался на странице сбора как
«2 человека уже помогли». Исторические значения приводятся в соответствие
с тем, что видно из самих пожертвований.
"""

from django.db import migrations
from django.db.models import Count


def recount(apps, schema_editor):
    Fundraise = apps.get_model('main', 'Fundraise')
    Donation = apps.get_model('main', 'Donation')

    counts = dict(
        Donation.objects
        .filter(refunded_at__isnull=True)
        .values('fundraise')
        .annotate(people=Count('donor', distinct=True))
        .values_list('fundraise', 'people')
    )

    for fundraise in Fundraise.objects.all():
        actual = counts.get(fundraise.pk, 0)
        if fundraise.donors_count != actual:
            fundraise.donors_count = actual
            fundraise.save(update_fields=['donors_count'])


def noop(apps, schema_editor):
    """Откат не нужен: прежние значения были неверны и восстанавливать их незачем."""


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0019_email_confirmation'),
    ]

    operations = [
        migrations.RunPython(recount, noop),
    ]
