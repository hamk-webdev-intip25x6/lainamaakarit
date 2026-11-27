from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Kirja(models.Model):
    kirja_id = models.AutoField(primary_key=True)
    nimi = models.CharField("Nimi", max_length=255)
    tekija = models.CharField("Tekijä", max_length=255)
    tehty = models.IntegerField("Julkaisuvuosi")
    avainsana = models.CharField("Avainsana", max_length=255, blank=True)
    kategoria = models.CharField("Kategoria", max_length=100, blank=True, default="")
    kieli = models.CharField("Kieli", max_length=50, blank=True, default="")
    kuvaus = models.TextField("Kuvaus", blank=True, default="")
    salattu_tiedosto = models.CharField(
        "Salattu PDF-tiedosto",
        max_length=255,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "kirjat"
        verbose_name = "Kirja"
        verbose_name_plural = "Kirjat"
        ordering = ["nimi"]

    def aktiivinen_laina(self):
        return (
            self.lainat.filter(laina_loppu__gt=timezone.now())
            .select_related("kayttaja")
            .first()
        )

    @property
    def on_saatavilla(self) -> bool:
        return self.aktiivinen_laina() is None

    def __str__(self) -> str:
        return f"{self.nimi} ({self.tekija})"


class Kommentti(models.Model):
    kommentti_id = models.AutoField(primary_key=True)
    kayttaja = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="kommentit",
        verbose_name="Käyttäjä",
    )
    kirja = models.ForeignKey(
        Kirja,
        on_delete=models.CASCADE,
        related_name="kommentit",
        verbose_name="Kirja",
    )
    teksti = models.TextField("Teksti")
    arvosana = models.IntegerField(
        "Arvosana",
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )

    class Meta:
        db_table = "kommentit"
        verbose_name = "Kommentti"
        verbose_name_plural = "Kommentit"

    def __str__(self) -> str:
        return f"{self.kirja.nimi} - {self.kayttaja.username} ({self.arvosana}/5)"


class Laina(models.Model):
    laina_id = models.AutoField(primary_key=True)
    kirja = models.ForeignKey(
        Kirja,
        on_delete=models.CASCADE,
        related_name="lainat",
        verbose_name="Kirja",
    )
    kayttaja = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lainat",
        verbose_name="Käyttäjä",
    )
    laina_alku = models.DateTimeField("Lainan alku", default=timezone.now)
    laina_loppu = models.DateTimeField("Lainan loppu")
    laina_tunniste = models.CharField(
        "Lainatunniste",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )
    valiaikainen_tiedosto = models.CharField(
        "Väliaikainen PDF-tiedosto",
        max_length=255,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "lainat"
        verbose_name = "Laina"
        verbose_name_plural = "Lainat"
        ordering = ["-laina_alku"]

    @property
    def on_voimassa(self) -> bool:
        return self.laina_loppu > timezone.now()

    def clean(self) -> None:
        super().clean()
        if self.laina_alku and self.laina_loppu and self.laina_loppu < self.laina_alku:
            raise ValidationError(
                {"laina_loppu": "Lainan lopetusajan täytyy olla alkamisajan jälkeen."}
            )

    def __str__(self) -> str:
        return f"{self.kirja.nimi} -> {self.kayttaja.username}"
