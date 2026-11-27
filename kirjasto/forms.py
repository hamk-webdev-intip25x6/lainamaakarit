from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Kommentti, Laina

User = get_user_model()

DATETIME_INPUT_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
]


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


class ArvosteluLomake(forms.ModelForm):
    """Form for creating or updating a 1–5 star book review."""

    TAHTI_VALINNAT = [
        (1, "1 tähti"),
        (2, "2 tähteä"),
        (3, "3 tähteä"),
        (4, "4 tähteä"),
        (5, "5 tähteä"),
    ]

    arvosana = forms.TypedChoiceField(
        label="Tähtiarvosana (1–5)",
        choices=TAHTI_VALINNAT,
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Valitse tähtiarvosana väliltä 1–5.",
            "invalid_choice": "Arvosanan täytyy olla kokonaisluku väliltä 1–5.",
        },
    )

    class Meta:
        model = Kommentti
        fields = ("arvosana", "teksti")
        labels = {
            "teksti": "Arvostelu",
        }
        widgets = {
            "teksti": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": Kommentti.MAX_TEKSTI_PITUUS,
                    "placeholder": "Kirjoita arvostelusi tästä kirjasta...",
                }
            ),
        }

    def clean_teksti(self) -> str:
        teksti = self.cleaned_data.get("teksti", "").strip()
        if not teksti:
            raise ValidationError("Arvosteluteksti ei voi olla tyhjä.")
        if len(teksti) > Kommentti.MAX_TEKSTI_PITUUS:
            raise ValidationError(
                f"Arvostelu saa olla enintään {Kommentti.MAX_TEKSTI_PITUUS} merkkiä pitkä."
            )
        return teksti


class LainaMuokkausLomake(forms.ModelForm):
    """Admin form in Huolto for modifying book loan attributes and start/end times."""

    laina_alku = forms.DateTimeField(
        label="Lainan alkuaika",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "step": "1"},
            format="%Y-%m-%dT%H:%M:%S",
        ),
    )
    laina_loppu = forms.DateTimeField(
        label="Lainan loppuaika",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "step": "1"},
            format="%Y-%m-%dT%H:%M:%S",
        ),
    )

    class Meta:
        model = Laina
        fields = ("kirja", "kayttaja", "laina_alku", "laina_loppu")
        labels = {
            "kirja": "Kirja",
            "kayttaja": "Lainaaja",
        }

    def clean(self):
        cleaned = super().clean()
        alku = cleaned.get("laina_alku")
        loppu = cleaned.get("laina_loppu")
        kirja = cleaned.get("kirja")

        if alku and loppu and loppu < alku:
            self.add_error(
                "laina_loppu",
                "Lainan lopetusajan täytyy olla alkamisajan jälkeen.",
            )

        if kirja and loppu and loppu > timezone.now():
            muu_aktiivinen = (
                Laina.objects.filter(kirja=kirja, laina_loppu__gt=timezone.now())
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if muu_aktiivinen:
                self.add_error(
                    "kirja",
                    "Tällä kirjalla on jo toinen voimassa oleva laina.",
                )
        return cleaned
