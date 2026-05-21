from django import forms
from .models import BusBooking


class BusBookingForm(forms.ModelForm):
    class Meta:
        model = BusBooking
        fields = [
            "customer_name",
            "customer_phone",
            "date_from",
            "time_from",
            "hours",
            "estimated_km",
            "note",
        ]


from .models import SupportTicket, SupportMessage, SupportPresetQuestion


class SupportTicketForm(forms.ModelForm):
    initial_message = forms.CharField(widget=forms.Textarea(attrs={"rows":3}), required=True, label="Опис проблеми")

    class Meta:
        model = SupportTicket
        fields = ["preset", "subject"]


class SupportMessageForm(forms.ModelForm):
    class Meta:
        model = SupportMessage
        fields = ["text", "attachment"]
        widgets = {
            "text": forms.Textarea(attrs={"rows":3, "placeholder":"Напишіть повідомлення..."}),
            "attachment": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }


from django.forms import ModelForm
from django.forms.models import inlineformset_factory
from .models import Ticket, Passenger
from .models import Profile
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


class TicketEditForm(ModelForm):
    class Meta:
        model = Ticket
        fields = [
            'from_city', 'to_city', 'travel_date', 'passengers', 'total_price', 'paid'
        ]


PassengerFormSet = inlineformset_factory(Ticket, Passenger, fields=('first_name', 'last_name'), extra=0, can_delete=True)


class ProfileForm(ModelForm):
    class Meta:
        model = Profile
        fields = ['avatar', 'phone']


class SupportUserForm(ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'is_active']


class RequestPasswordChangeForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput, label='Новий пароль', min_length=8)
    new_password2 = forms.CharField(widget=forms.PasswordInput, label='Повторіть пароль', min_length=8)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password')
        p2 = cleaned.get('new_password2')
        if not p1 or not p2:
            raise forms.ValidationError('Заповніть обидва поля пароля')
        if p1 != p2:
            raise forms.ValidationError('Паролі не співпадають')
        try:
            validate_password(p1)
        except ValidationError as e:
            raise forms.ValidationError(e.messages)
        return cleaned