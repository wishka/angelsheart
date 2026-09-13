import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import models, transaction
from django.template.loader import TemplateDoesNotExist, render_to_string
from django.utils import timezone

logger = logging.getLogger('withdrawals')


def luhn_valid(card_number):
    """Проверка контрольной суммы номера карты по алгоритму Луна."""
    digits = [int(ch) for ch in card_number if ch.isdigit()]
    if not digits:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


class MassWithdrawalService:
    """Сервис для массовых выплат"""
    
    def __init__(self):
        self.commission_percent = getattr(settings, 'WITHDRAWAL_COMMISSION', 0)
    
    def process_withdrawal(self, withdrawal):
        """
        Обработка одной заявки на вывод

        Args:
            withdrawal: Объект WithdrawalRequest

        Returns:
            dict: Результат обработки
        """
        try:
            # Логируем начало обработки
            logger.info(f"Начало обработки заявки #{withdrawal.id} "
                        f"({withdrawal.user.username}, {withdrawal.amount} ₽)")
            
            # Выбор провайдера в зависимости от способа вывода
            provider = self._get_provider(withdrawal.payment_method)
            
            # Выполнение выплаты
            result = provider.process_withdrawal(
                user_id=withdrawal.user.id,
                amount=withdrawal.amount,
                details=withdrawal.payment_details
            )
            
            if result['success']:
                # complete() проверяет, что заявка ещё удерживает средства,
                # и не даёт повторно завершить уже закрытую заявку
                withdrawal.complete(
                    admin_user=withdrawal.processed_by,
                    transaction_id=result.get('payout_id'),
                )

                self._send_withdrawal_notification(withdrawal, success=True)

                logger.info(
                    'Заявка #%s выполнена, транзакция %s',
                    withdrawal.id, result.get('payout_id'),
                )

                return {
                    'success': True,
                    'message': 'Выплата выполнена успешно',
                    'transaction_id': result.get('payout_id'),
                }

            # Выплата не прошла. mark_failed возвращает удержанную сумму
            # на баланс: раньше статус менялся на несуществующий 'failed',
            # а деньги пользователю не возвращались и просто исчезали.
            error = result.get('error', 'Ошибка при выплате')
            withdrawal.mark_failed(error)

            logger.error('Ошибка обработки заявки #%s: %s', withdrawal.id, error)
            self._send_withdrawal_notification(withdrawal, success=False)
            self._send_admin_alert(withdrawal, error)

            return {'success': False, 'message': error}

        except Exception as exc:
            logger.exception('Критическая ошибка при обработке заявки #%s', withdrawal.id)

            # Средства обязаны вернуться к пользователю и при системном сбое
            withdrawal.mark_failed(f'Системная ошибка: {exc}')
            self._send_admin_alert(withdrawal, str(exc))

            return {'success': False, 'message': 'Системная ошибка при выплате'}
    
    def process_mass_withdrawals(self, withdrawal_ids, admin_user=None):
        """
        Массовая обработка заявок на вывод

        Args:
            withdrawal_ids: Список ID заявок
            admin_user: Пользователь-администратор, инициировавший выплату

        Returns:
            dict: Результаты обработки
        """
        from main.models import WithdrawalRequest
        
        results = {
            'total': len(withdrawal_ids),
            'success': 0,
            'failed': 0,
            'details': []
        }
        
        for withdrawal_id in withdrawal_ids:
            try:
                withdrawal = WithdrawalRequest.objects.get(
                    id=withdrawal_id,
                    status='processing'
                )
                
                if admin_user:
                    withdrawal.processed_by = admin_user
                    withdrawal.save()
                
                result = self.process_withdrawal(withdrawal)
                
                if result['success']:
                    results['success'] += 1
                else:
                    results['failed'] += 1
                
                results['details'].append({
                    'id': withdrawal_id,
                    'success': result['success'],
                    'message': result['message']
                })
            
            except WithdrawalRequest.DoesNotExist:
                results['details'].append({
                    'id': withdrawal_id,
                    'success': False,
                    'message': 'Заявка не найдена или не в статусе "В обработке"'
                })
                results['failed'] += 1
        
        # Отправляем отчет администратору
        if admin_user and results['total'] > 0:
            self._send_admin_report(admin_user, results)
        
        return results
    
    def _get_provider(self, payment_method):
        """
        Провайдер выплаты.

        Раньше словарь перечислял SBPProvider и StripeProvider, которые были
        просто псевдонимами (SBPProvider = YooKassaProvider,
        StripeProvider = MockPaymentProvider) — то есть выплата «через Stripe»
        молча уходила в заглушку и рапортовала об успехе.
        """
        from main.payments.yookassa import YooKassaProvider

        if payment_method not in ('card', 'sbp', 'yoomoney'):
            raise ValueError(f'Неподдерживаемый способ выплаты: {payment_method}')
        return YooKassaProvider()
    
    def _send_withdrawal_notification(self, withdrawal, success=True):
        """
        Уведомление пользователю о статусе вывода.

        Шаблоны писем в проекте отсутствовали: render_to_string бросал
        TemplateDoesNotExist, исключение гасилось общим except, и письма
        не отправлялись никогда. Теперь шаблоны есть, а отсутствие шаблона
        не оставляет пользователя без уведомления — уходит текстовое письмо.
        """
        if not withdrawal.user.email:
            return

        if success:
            subject = f'Вывод средств #{withdrawal.id} выполнен'
            template = 'emails/withdrawal_success.html'
            fallback = (
                f'Заявка на вывод #{withdrawal.id} на сумму {withdrawal.amount} ₽ выполнена.'
            )
        else:
            subject = f'Вывод средств #{withdrawal.id} не выполнен'
            template = 'emails/withdrawal_failed.html'
            fallback = (
                f'Заявка на вывод #{withdrawal.id} на сумму {withdrawal.amount} ₽ '
                f'не выполнена, средства возвращены на баланс. '
                f'Причина: {withdrawal.comment or "не указана"}.'
            )

        html_message = None
        try:
            html_message = render_to_string(template, {
                'withdrawal': withdrawal,
                'user': withdrawal.user,
                'site_url': settings.SITE_URL,
            })
        except TemplateDoesNotExist:
            logger.warning('Шаблон письма %s не найден, отправляю текстовое письмо', template)

        try:
            send_mail(
                subject=subject,
                message=fallback,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[withdrawal.user.email],
                html_message=html_message,
                fail_silently=False,
            )
        except Exception:
            logger.exception('Не удалось отправить уведомление по заявке #%s', withdrawal.id)
    
    def _send_admin_alert(self, withdrawal, error_message):
        """Отправка оповещения администратору об ошибке"""
        try:
            admins = settings.ADMINS if hasattr(settings, 'ADMINS') else []
            admin_emails = [admin[1] for admin in admins]
            
            if admin_emails:
                subject = f'⚠️ Ошибка выплаты #{withdrawal.id}'
                html_message = render_to_string('emails/admin_withdrawal_error.html', {
                    'withdrawal': withdrawal,
                    'error': error_message,
                    'admin_url': f"{settings.SITE_URL}/admin/main/withdrawalrequest/{withdrawal.id}/change/"
                })
                
                send_mail(
                    subject=subject,
                    message='',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=admin_emails,
                    html_message=html_message,
                    fail_silently=True,
                )
        except Exception as e:
            logger.warning(f"Не удалось отправить оповещение администраторам: {e}")
    
    def _send_admin_report(self, admin_user, results):
        """Отправка отчета администратору о массовой выплате"""
        try:
            subject = f'📊 Отчет о массовой выплате'
            html_message = render_to_string('emails/admin_withdrawal_report.html', {
                'admin': admin_user,
                'results': results,
                'site_url': settings.SITE_URL,
            })
            
            send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin_user.email],
                html_message=html_message,
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить отчет администратору: {e}")


class WithdrawalValidator:
    """Класс для валидации заявок на вывод"""
    
    @staticmethod
    def validate_withdrawal_request(user, amount, payment_details, payment_method):
        """
        Проверка заявки на вывод перед созданием.

        Раньше этот метод падал с `NameError: name 'self' is not defined`
        (обращение к self внутри @staticmethod) и с `NameError: models`
        (отсутствовал импорт) — и при этом не вызывался из вью вообще,
        поэтому лимиты верификации не действовали.

        Returns:
            tuple: (is_valid, error_message)
        """
        from main.models import WithdrawalRequest
        from main.payments.verification import KYCService

        amount = Decimal(amount)

        if amount < settings.MIN_WITHDRAWAL_AMOUNT:
            return False, f'Минимальная сумма вывода — {settings.MIN_WITHDRAWAL_AMOUNT} ₽'

        if amount > settings.MAX_WITHDRAWAL_AMOUNT:
            return False, f'Максимальная сумма вывода — {settings.MAX_WITHDRAWAL_AMOUNT} ₽'

        if user.balance.amount < amount:
            return False, 'Недостаточно средств на балансе'

        # Лимиты по уровню верификации — единая реализация в KYCService
        allowed, error = KYCService.check_withdrawal_limit(user, amount)
        if not allowed:
            return False, error

        today = timezone.now().date()
        today_requests = WithdrawalRequest.objects.filter(
            user=user,
            created_at__date=today,
            status__in=WithdrawalRequest.HOLDING_STATUSES,
        ).count()

        if today_requests >= 3:
            return False, 'Вы можете создать не более 3 активных заявок в день'

        validation = WithdrawalValidator._validate_payment_details(payment_details, payment_method)
        if not validation['is_valid']:
            return False, validation['error']

        return True, None


    @staticmethod
    def _validate_payment_details(details, method):
        """Проверка реквизитов для вывода"""
        if method == 'card':
            card_number = ''.join(ch for ch in str(details.get('card_number') or '') if ch.isdigit())
            if len(card_number) not in (16, 18):
                return {'is_valid': False, 'error': 'Неверный номер карты'}
            if not luhn_valid(card_number):
                # Контрольная сумма отсекает опечатки до обращения
                # к платёжной системе
                return {'is_valid': False, 'error': 'Номер карты указан с ошибкой'}

            card_holder = str(details.get('card_holder') or '').strip()
            if len(card_holder) < 3:
                return {'is_valid': False, 'error': 'Укажите имя держателя карты'}

            expiry = str(details.get('expiry_date') or '').strip()
            if len(expiry) != 5 or expiry[2] != '/':
                return {'is_valid': False, 'error': 'Неверный формат срока действия (MM/YY)'}
            month, year = expiry[:2], expiry[3:]
            if not (month.isdigit() and year.isdigit() and 1 <= int(month) <= 12):
                return {'is_valid': False, 'error': 'Неверный срок действия карты'}
            # Карта не должна быть просрочена
            now = timezone.now()
            if (2000 + int(year), int(month)) < (now.year, now.month):
                return {'is_valid': False, 'error': 'Срок действия карты истёк'}

        elif method == 'sbp':
            phone = ''.join(ch for ch in str(details.get('phone_number') or '') if ch.isdigit())
            if len(phone) not in (10, 11):
                return {'is_valid': False, 'error': 'Неверный номер телефона'}

        elif method == 'yoomoney':
            wallet = str(details.get('wallet_number') or '').strip()
            if not wallet.isdigit() or not wallet.startswith('41001') or len(wallet) < 14:
                return {'is_valid': False, 'error': 'Неверный номер кошелька ЮMoney'}

        else:
            return {'is_valid': False, 'error': 'Неподдерживаемый способ вывода'}

        return {'is_valid': True, 'error': None}


class WithdrawalReport:
    """Класс для формирования отчетов по выводам"""
    
    @staticmethod
    def generate_daily_report(date=None):
        """Формирование дневного отчета по выводам"""
        from main.models import WithdrawalRequest
        
        if date is None:
            date = timezone.now().date()
        
        withdrawals = WithdrawalRequest.objects.filter(created_at__date=date)
        
        report = {
            'date': date,
            'total_requests': withdrawals.count(),
            'pending': withdrawals.filter(status='pending').count(),
            'processing': withdrawals.filter(status='processing').count(),
            'completed': withdrawals.filter(status='completed').count(),
            'rejected': withdrawals.filter(status='rejected').count(),
            'cancelled': withdrawals.filter(status='cancelled').count(),
            'total_amount': withdrawals.aggregate(total=models.Sum('amount'))['total'] or 0,
            'completed_amount': withdrawals.filter(status='completed').aggregate(total=models.Sum('amount'))[
                                    'total'] or 0,
            'by_method': {},
        }
        
        # Статистика по способам вывода
        for method, _ in WithdrawalRequest.PAYMENT_METHOD_CHOICES:
            report['by_method'][method] = {
                'count': withdrawals.filter(payment_method=method).count(),
                'amount': withdrawals.filter(payment_method=method).aggregate(total=models.Sum('amount'))['total'] or 0
            }
        
        return report
    
    @staticmethod
    def generate_user_report(user, days=30):
        """Формирование отчета по выводам пользователя"""
        from main.models import WithdrawalRequest
        from datetime import timedelta
        
        start_date = timezone.now().date() - timedelta(days=days)
        withdrawals = WithdrawalRequest.objects.filter(
            user=user,
            created_at__date__gte=start_date
        )
        
        report = {
            'user': user.username,
            'period': f'Последние {days} дней',
            'total_requests': withdrawals.count(),
            'total_amount': withdrawals.aggregate(total=models.Sum('amount'))['total'] or 0,
            'completed_amount': withdrawals.filter(status='completed').aggregate(total=models.Sum('amount'))[
                                    'total'] or 0,
            'average_amount': withdrawals.aggregate(avg=models.Avg('amount'))['avg'] or 0,
            'requests': [
                {
                    'id': w.id,
                    'amount': w.amount,
                    'status': w.status,
                    'created_at': w.created_at,
                    'processed_at': w.processed_at,
                }
                for w in withdrawals.order_by('-created_at')
            ]
        }
        
        return report