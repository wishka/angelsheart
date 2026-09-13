from django.conf import settings


def cookie_consent(request):
    """Контекстный процессор для проверки согласия на cookies"""
    consent_given = request.COOKIES.get('cookie_consent', False)
    return {
        'cookie_consent_given': consent_given == 'true',
    }


def legal(request):
    """
    Реквизиты оператора и параметры сервиса для юридических документов.

    Раньше политика и оферта не содержали сведений об операторе вовсе,
    хотя ч. 2 ст. 10 149-ФЗ требует раскрывать их на сайте, а ч. 4 ст. 9
    152-ФЗ — указывать в согласии наименование и адрес оператора.

    Значения задаются в настройках, чтобы не расходиться между четырьмя
    документами при смене реквизитов.
    """
    return {
        'operator': settings.OPERATOR,
        'commission_percent': settings.DONATION_COMMISSION_PERCENT,
        'min_donation': settings.MIN_DONATION_AMOUNT,
        'max_donation': settings.MAX_DONATION_AMOUNT,
        'min_withdrawal': settings.MIN_WITHDRAWAL_AMOUNT,
        'max_withdrawal': settings.MAX_WITHDRAWAL_AMOUNT,
        'max_topup': settings.MAX_TOPUP_AMOUNT,
        'consent_version': settings.LEGAL_DOCS_VERSION,
        'consent_updated': settings.LEGAL_DOCS_UPDATED,
        'retention': settings.DATA_RETENTION,
        # Шаблоны (topup.html, base.html) ссылались на USE_REAL_PAYMENTS,
        # которого в контексте не было: отсутствующая переменная в {% if %}
        # всегда ложна, поэтому в боевом режиме страница пополнения уверяла,
        # что «платежи не списывают реальные деньги», а индикатор в шапке
        # всегда показывал TEST.
        'USE_REAL_PAYMENTS': settings.USE_REAL_PAYMENTS,
        'allow_simulated_topup': settings.ALLOW_SIMULATED_TOPUP,
        'min_topup': settings.MIN_TOPUP_AMOUNT,
    }
