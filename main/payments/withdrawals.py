import logging
import uuid
from decimal import Decimal
from datetime import datetime
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger('withdrawals')


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
                # Обновляем статус заявки
                withdrawal.status = 'completed'
                withdrawal.transaction_id = result.get('payout_id')
                withdrawal.processed_at = timezone.now()
                withdrawal.save()
                
                # Отправляем уведомление пользователю
                self._send_withdrawal_notification(withdrawal, success=True)
                
                logger.info(f"Заявка #{withdrawal.id} успешно обработана. "
                            f"Транзакция: {result.get('payout_id')}")
                
                return {
                    'success': True,
                    'message': 'Выплата выполнена успешно',
                    'transaction_id': result.get('payout_id')
                }
            else:
                # Ошибка при выплате
                withdrawal.status = 'failed'
                withdrawal.comment = result.get('error', 'Ошибка при выплате')
                withdrawal.save()
                
                logger.error(f"Ошибка обработки заявки #{withdrawal.id}: {result.get('error')}")
                
                # Отправляем уведомление об ошибке администратору
                self._send_admin_alert(withdrawal, result.get('error'))
                
                return {
                    'success': False,
                    'message': result.get('error', 'Ошибка при выплате')
                }
        
        except Exception as e:
            logger.exception(f"Критическая ошибка при обработке заявки #{withdrawal.id}: {str(e)}")
            
            withdrawal.status = 'failed'
            withdrawal.comment = f'Системная ошибка: {str(e)}'
            withdrawal.save()
            
            return {
                'success': False,
                'message': str(e)
            }
    
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
        """Получение провайдера для конкретного способа вывода"""
        from main.payments.yookassa import YooKassaProvider, SBPProvider, StripeProvider
        
        providers = {
            'card': YooKassaProvider,
            'sbp': SBPProvider,
            'yoomoney': YooKassaProvider,
            'crypto': YooKassaProvider,
        }
        
        provider_class = providers.get(payment_method, YooKassaProvider)
        return provider_class()
    
    def _send_withdrawal_notification(self, withdrawal, success=True):
        """Отправка уведомления пользователю о статусе вывода"""
        try:
            if success:
                subject = f'✅ Вывод средств #{withdrawal.id} выполнен'
                template = 'emails/withdrawal_success.html'
            else:
                subject = f'❌ Вывод средств #{withdrawal.id} отклонен'
                template = 'emails/withdrawal_failed.html'
            
            html_message = render_to_string(template, {
                'withdrawal': withdrawal,
                'user': withdrawal.user,
                'site_url': settings.SITE_URL,
            })
            
            send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[withdrawal.user.email],
                html_message=html_message,
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {withdrawal.user.email}: {e}")
    
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
        Проверка заявки на вывод перед созданием

        Returns:
            tuple: (is_valid, error_message)
        """
        from main.models import WithdrawalRequest, UserVerification
        
        # 1. Проверка минимальной суммы
        if amount < settings.MIN_WITHDRAWAL_AMOUNT:
            return False, f"Минимальная сумма вывода — {settings.MIN_WITHDRAWAL_AMOUNT} ₽"
        
        # 2. Проверка максимальной суммы
        if amount > settings.MAX_WITHDRAWAL_AMOUNT:
            return False, f"Максимальная сумма вывода — {settings.MAX_WITHDRAWAL_AMOUNT} ₽"
        
        # 3. Проверка баланса
        if user.balance.amount < amount:
            return False, "Недостаточно средств на балансе"
        
        # 4. Проверка лимитов по верификации
        try:
            verification = user.verification
            level = verification.level
        except UserVerification.DoesNotExist:
            level = 'unverified'
        
        limits = self._get_limits_by_level(level)
        
        if amount > limits['single_withdrawal']:
            return False, f"Максимальная сумма одной выплаты для вашего уровня верификации — {limits['single_withdrawal']} ₽. Повысьте уровень верификации."
        
        # 5. Проверка дневного лимита
        today = timezone.now().date()
        daily_total = WithdrawalRequest.objects.filter(
            user=user,
            created_at__date=today,
            status__in=['pending', 'processing', 'completed']
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        if daily_total + amount > limits['daily_withdrawal']:
            return False, f"Дневной лимит вывода для вашего уровня верификации — {limits['daily_withdrawal']} ₽"
        
        # 6. Проверка количества заявок в день
        today_requests = WithdrawalRequest.objects.filter(
            user=user,
            created_at__date=today,
            status__in=['pending', 'processing']
        ).count()
        
        if today_requests >= 3:
            return False, "Вы можете создать не более 3 активных заявок в день"
        
        # 7. Проверка реквизитов
        validation = WithdrawalValidator._validate_payment_details(payment_details, payment_method)
        if not validation['is_valid']:
            return False, validation['error']
        
        return True, None
    
    @staticmethod
    def _get_limits_by_level(level):
        """Лимиты в зависимости от уровня верификации"""
        limits = {
            'unverified': {
                'daily_withdrawal': 1000,
                'monthly_withdrawal': 5000,
                'single_withdrawal': 500,
                'daily_payment': 10000,
            },
            'basic': {
                'daily_withdrawal': 10000,
                'monthly_withdrawal': 50000,
                'single_withdrawal': 5000,
                'daily_payment': 50000,
            },
            'full': {
                'daily_withdrawal': 100000,
                'monthly_withdrawal': None,
                'single_withdrawal': 50000,
                'daily_payment': 500000,
            },
        }
        return limits.get(level, limits['unverified'])
    
    @staticmethod
    def _validate_payment_details(details, method):
        """Проверка реквизитов для вывода"""
        if method == 'card':
            card_number = details.get('card_number', '').replace(' ', '')
            if len(card_number) not in [16, 18]:
                return {'is_valid': False, 'error': 'Неверный номер карты'}
            
            card_holder = details.get('card_holder', '').upper()
            if len(card_holder) < 3:
                return {'is_valid': False, 'error': 'Укажите имя держателя карты'}
            
            expiry = details.get('expiry_date', '')
            if len(expiry) != 5 or expiry[2] != '/':
                return {'is_valid': False, 'error': 'Неверный формат срока действия (MM/YY)'}
        
        elif method == 'sbp':
            phone = details.get('phone_number', '').replace('+', '').replace('-', '').replace(' ', '')
            if len(phone) < 10:
                return {'is_valid': False, 'error': 'Неверный номер телефона'}
        
        elif method == 'yoomoney':
            wallet = details.get('wallet_number', '')
            if not wallet.startswith('41001') or len(wallet) < 14:
                return {'is_valid': False, 'error': 'Неверный номер кошелька ЮMoney'}
        
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