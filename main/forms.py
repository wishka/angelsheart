from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Transaction


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