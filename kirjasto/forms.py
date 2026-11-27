from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

User = get_user_model()


class SignupForm(forms.ModelForm):
    """Custom user registration form with username and encrypted password."""

    password = forms.CharField(
        label="Salasana",
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )

    class Meta:
        model = User
        fields = ("username",)
        labels = {
            "username": "Käyttäjänimi",
        }
        widgets = {
            "username": forms.TextInput(
                attrs={"autocomplete": "username", "autofocus": True}
            ),
        }

    def clean_username(self) -> str:
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise ValidationError("Käyttäjänimi ei voi olla tyhjä.")
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Tämä käyttäjänimi on jo käytössä.")
        return username

    def _post_clean(self) -> None:
        super()._post_clean()
        password = self.cleaned_data.get("password")
        if password:
            try:
                password_validation.validate_password(password, self.instance)
            except ValidationError as error:
                self.add_error("password", error)

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user
