"""
Контроль сроков уведомления об инцидентах с персональными данными.

Часть 3.1 ст. 21 152-ФЗ: 24 часа на первичное уведомление Роскомнадзора
и 72 часа на уведомление о результатах внутреннего расследования. Отсчёт —
с момента, когда об инциденте стало известно.

Команда не отправляет уведомления: РКН принимает их через свой портал,
и автоматическая отправка в государственный орган от имени оператора —
не то, что стоит делать по cron. Она делает другое: не даёт пропустить
срок и печатает готовый текст уведомления, чтобы в час инцидента его
не пришлось сочинять с нуля.

Ставится в cron с частотой не реже часа:
    0 * * * *  cd /path/to/project && python manage.py breach_check --alert

Флаг --alert отправляет письмо ответственному за обработку ПДн, если
до срока осталось меньше порога или срок уже пропущен.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from main.models import DataBreachIncident


class Command(BaseCommand):
    help = 'Проверяет сроки уведомления об инцидентах с ПДн (24/72 часа, ст. 21 152-ФЗ)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--alert', action='store_true',
            help='Отправить письмо ответственному, если срок близок или пропущен',
        )
        parser.add_argument(
            '--warn-hours', type=float, default=6,
            help='За сколько часов до срока предупреждать (по умолчанию 6)',
        )
        parser.add_argument(
            '--draft', type=int, default=None,
            help='Напечатать текст уведомления по инциденту с этим номером',
        )

    def handle(self, *args, **options):
        if options['draft'] is not None:
            self._print_draft(options['draft'])
            return

        warn_hours = options['warn_hours']
        incidents = [i for i in DataBreachIncident.objects.all() if i.is_open]

        if not incidents:
            self.stdout.write('Открытых инцидентов нет.')
            return

        urgent = []
        for incident in incidents:
            lines = [f'#{incident.pk} {incident.summary} (обнаружен {incident.detected_at:%d.%m.%Y %H:%M})']

            if incident.initial_notice_sent_at:
                lines.append(f'  первичное уведомление: отправлено '
                             f'{incident.initial_notice_sent_at:%d.%m.%Y %H:%M} '
                             f'{incident.initial_notice_reference or ""}'.rstrip())
            elif incident.initial_overdue:
                lines.append(self.style.ERROR('  первичное уведомление (24 ч): СРОК ПРОПУЩЕН'))
                urgent.append(incident)
            else:
                left = incident.hours_to_initial
                text = f'  первичное уведомление (24 ч): осталось {left} ч'
                lines.append(self.style.WARNING(text) if left <= warn_hours else text)
                if left <= warn_hours:
                    urgent.append(incident)

            if incident.final_notice_sent_at:
                lines.append(f'  уведомление о расследовании: отправлено '
                             f'{incident.final_notice_sent_at:%d.%m.%Y %H:%M} '
                             f'{incident.final_notice_reference or ""}'.rstrip())
            elif incident.final_overdue:
                lines.append(self.style.ERROR('  уведомление о расследовании (72 ч): СРОК ПРОПУЩЕН'))
                urgent.append(incident)
            else:
                left = incident.hours_to_final
                text = f'  уведомление о расследовании (72 ч): осталось {left} ч'
                lines.append(self.style.WARNING(text) if left <= warn_hours else text)
                if left <= warn_hours:
                    urgent.append(incident)

            for line in lines:
                self.stdout.write(line)

        if options['alert'] and urgent:
            self._alert(set(urgent))

    def _alert(self, incidents):
        recipient = settings.OPERATOR.get('dpo_email') or settings.OPERATOR.get('support_email')
        if not recipient:
            self.stderr.write('Некому отправить оповещение: OPERATOR_DPO_EMAIL не заполнен')
            return

        body = ['Приближается или пропущен срок уведомления об инциденте с ПДн.', '']
        for incident in incidents:
            body.append(f'#{incident.pk} {incident.summary}')
            body.append(f'  обнаружен: {incident.detected_at:%d.%m.%Y %H:%M}')
            body.append(f'  24 ч: до {incident.initial_notice_deadline:%d.%m.%Y %H:%M}'
                        f'{" — ПРОПУЩЕН" if incident.initial_overdue else ""}')
            body.append(f'  72 ч: до {incident.final_notice_deadline:%d.%m.%Y %H:%M}'
                        f'{" — ПРОПУЩЕН" if incident.final_overdue else ""}')
            body.append('')
        body.append('Текст уведомления: python manage.py breach_check --draft <номер>')

        send_mail(
            subject='Срок уведомления об инциденте с персональными данными',
            message='\n'.join(body),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f'Оповещение отправлено на {recipient}'))

    def _print_draft(self, pk):
        """
        Заготовка уведомления.

        Поля соответствуют составу сведений из ч. 3.1 ст. 21 152-ФЗ.
        Это черновик для человека, а не готовая форма портала РКН.
        """
        try:
            incident = DataBreachIncident.objects.get(pk=pk)
        except DataBreachIncident.DoesNotExist:
            self.stderr.write(f'Инцидент #{pk} не найден')
            return

        operator = settings.OPERATOR
        now = timezone.localtime()

        self.stdout.write(f"""
УВЕДОМЛЕНИЕ ОБ ИНЦИДЕНТЕ С ПЕРСОНАЛЬНЫМИ ДАННЫМИ
(ч. 3.1 ст. 21 Федерального закона от 27.07.2006 № 152-ФЗ)

Оператор: {operator.get('name') or '— НЕ ЗАПОЛНЕНО —'}
ИНН: {operator.get('inn') or '— НЕ ЗАПОЛНЕНО —'}
Номер в реестре операторов: {operator.get('rkn_notice') or '— НЕ ЗАПОЛНЕНО —'}
Ответственный за обработку ПДн: {operator.get('dpo_name') or '— НЕ ЗАПОЛНЕНО —'}
Контакт: {operator.get('dpo_email') or operator.get('support_email') or '— НЕ ЗАПОЛНЕНО —'}

Дата и время составления: {now:%d.%m.%Y %H:%M}

1. Момент, когда оператору стало известно об инциденте:
   {timezone.localtime(incident.detected_at):%d.%m.%Y %H:%M}

2. Существо инцидента:
   {incident.description}

3. Категории затронутых персональных данных:
   {incident.data_categories}

4. Количество субъектов, персональные данные которых затронуты:
   {incident.affected_count}

5. Предполагаемая причина:
   {incident.suspected_cause or '— устанавливается —'}

6. Результаты внутреннего расследования:
   {incident.investigation_result or '— расследование продолжается —'}

7. Принятые меры по устранению последствий:
   {incident.measures_taken or '— принимаются —'}

8. Уведомление субъектов персональных данных:
   {('направлено ' + f'{timezone.localtime(incident.subjects_notified_at):%d.%m.%Y %H:%M}')
     if incident.subjects_notified_at else 'не направлялось'}

Сроки по данному инциденту:
   первичное уведомление (24 ч) — до {timezone.localtime(incident.initial_notice_deadline):%d.%m.%Y %H:%M}
   уведомление о расследовании (72 ч) — до {timezone.localtime(incident.final_notice_deadline):%d.%m.%Y %H:%M}

Уведомление подаётся через портал Роскомнадзора. После отправки внесите
входящий номер в карточку инцидента — без него соблюдение срока
доказать нечем.
""")
