from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Transaction, Fundraise, Donation


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']


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
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Сумма должна быть больше 0')
        if amount < 1:
            raise forms.ValidationError('Минимальная сумма пожертвования - 1 ₽')
        if amount > 1000000:
            raise forms.ValidationError('Максимальная сумма пожертвования 1 000 000 ₽')
        return amount
    
    def clean(self):
        cleaned_data = super().clean()
        # Эта проверка будет дополнена в view, так как там есть request.user
        return cleaned_data