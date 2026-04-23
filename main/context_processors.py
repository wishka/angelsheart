from django.conf import settings

def cookie_consent(request):
    """Контекстный процессор для проверки согласия на cookies"""
    consent_given = request.COOKIES.get('cookie_consent', False)
    return {
        'cookie_consent_given': consent_given == 'true',
    }