from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import Invitation, Membership, Organization, User


class SignupForm(forms.Form):
    organization_name = forms.CharField(
        max_length=200, label="Institution / organization name"
    )
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            validate_password(p1)
        return cleaned


class OrgSettingsForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name"]


class InviteForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["email", "role"]

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if self.org is not None:
            if Membership.objects.filter(org=self.org, user__email__iexact=email).exists():
                raise forms.ValidationError("That person is already a member.")
            if Invitation.objects.filter(
                org=self.org, email__iexact=email, accepted_at__isnull=True
            ).exists():
                raise forms.ValidationError("There is already a pending invite for that email.")
        return email


class AcceptInviteForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            validate_password(p1)
        return cleaned
