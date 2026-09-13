from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Donation, Fundraise, Transaction, WithdrawalRequest


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']

    def clean_email(self):
        # Email нужен для восстановления доступа и уведомлений о выплатах,
        # поэтому дубликаты недопустимы. Django по умолчанию их разрешает.
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Пользователь с таким email уже зарегистрирован')
        return email


class TransferForm(forms.ModelForm):
    receiver_username = forms.CharField(label='Кому (username)', max_length=150)
    
    class Meta:
        model = Transaction
        fields = ['amount', 'comment']
    
    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Сумма должна быть больше 0')
        return amount


class FundraiseForm(forms.ModelForm):
    """Форма создания сбора средств"""
    
    class Meta:
        model = Fundraise
        fields = ['title', 'description', 'category', 'target_amount', 'end_date', 'image_url']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например: Помощь в лечении...'}),
            'description': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'Расскажите подробнее о вашей цели...'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'target_amount': forms.NumberInput(
                attrs={'class': 'form-control', 'placeholder': 'Сумма в рублях', 'min': '1'}),
            'end_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'image_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://...'})
        }


class DonationForm(forms.ModelForm):
    """Форма пожертвования"""
    
    class Meta:
        model = Donation
        fields = ['amount', 'message', 'is_anonymous']
        widgets = {
            'amount': forms.NumberInput(
                attrs={'class': 'form-control', 'placeholder': 'Сумма в рублях', 'min': '1', 'step': '1'}),
            'message': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Напишите слова поддержки (необязательно)'}),
            'is_anonymous': forms.CheckboxInput(attrs={'class': 'form-check-input'})
        }
    
    def clean_amount(self):
        # Границы берутся из настроек, а не зашиты числами в трёх местах
        amount = self.cleaned_data['amount']
        if amount < settings.MIN_DONATION_AMOUNT:
            raise forms.ValidationError(
                f'Минимальная сумма пожертвования — {settings.MIN_DONATION_AMOUNT} ₽'
            )
        if amount > settings.MAX_DONATION_AMOUNT:
            raise forms.ValidationError(
                f'Максимальная сумма пожертвования — {settings.MAX_DONATION_AMOUNT} ₽'
            )
        return amount


class WithdrawalForm(forms.Form):
    payment_method = forms.ChoiceField(
        choices=WithdrawalRequest.PAYMENT_METHOD_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    amount = forms.DecimalField(
        min_value=settings.MIN_WITHDRAWAL_AMOUNT,
        max_value=settings.MAX_WITHDRAWAL_AMOUNT,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Сумма вывода'})
    )
    
    # Для карты
    card_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '0000 0000 0000 0000'})
    )
    card_holder = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'NAME SURNAME'})
    )
    expiry_date = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'MM/YY'})
    )
    
    # Для СБП
    phone_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+7 XXX XXX-XX-XX'})
    )
    
    # Для ЮMoney
    wallet_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '41001XXXXXXXXXX'})
    )
    
    # clean_amount удалён: границы уже заданы min_value/max_value поля
    # и брались из настроек, а дублирующая проверка повторяла числа руками

    def clean(self):
        cleaned_data = super().clean()
        payment_method = cleaned_data.get('payment_method')
        
        if payment_method == 'card':
            if not cleaned_data.get('card_number'):
                self.add_error('card_number', 'Введите номер карты')
            if not cleaned_data.get('card_holder'):
                self.add_error('card_holder', 'Введите имя держателя карты')
        elif payment_method == 'sbp':
            if not cleaned_data.get('phone_number'):
                self.add_error('phone_number', 'Введите номер телефона')
        elif payment_method == 'yoomoney':
            if not cleaned_data.get('wallet_number'):
                self.add_error('wallet_number', 'Введите номер кошелька')
        
        return cleaned_data