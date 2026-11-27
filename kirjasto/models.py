from statistics import mean, median

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
)
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
    listattu = models.BooleanField("Listattu kirjastossa", default=True)

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
        return self.listattu and self.aktiivinen_laina() is None

    def _hae_arvosanat(self) -> list[int]:
        if hasattr(self, "ladatut_kommentit"):
            return [k.arvosana for k in self.ladatut_kommentit]
        return list(self.kommentit.values_list("arvosana", flat=True))

    def arvosana_mediaani(self) -> float | None:
        """Calculate the median star rating (1–5) across all reviews for this book."""
        arvosanat = self._hae_arvosanat()
        if not arvosanat:
            return None
        return round(float(median(arvosanat)), 1)

    def arvosana_keskiarvo(self) -> float | None:
        """Calculate the arithmetic mean star rating (1–5) for this book."""
        arvosanat = self._hae_arvosanat()
        if not arvosanat:
            return None
        return round(float(mean(arvosanat)), 1)

    def arvostelujen_maara(self) -> int:
        if hasattr(self, "ladatut_kommentit"):
            return len(self.ladatut_kommentit)
        return self.kommentit.count()

    def taydet_tahdet(self) -> int:
        """Rounded integer star count (1–5) based on median grade for visual star display."""
        med = self.arvosana_mediaani()
        if med is None:
            return 0
        return int(round(med))

    def __str__(self) -> str:
        return f"{self.nimi} ({self.tekija})"


class Kommentti(models.Model):
    MAX_TEKSTI_PITUUS = 2000

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
    teksti = models.TextField(
        "Arvosteluteksti",
        max_length=MAX_TEKSTI_PITUUS,
        validators=[MaxLengthValidator(MAX_TEKSTI_PITUUS)],
    )
    arvosana = models.IntegerField(
        "Tähtiarvosana",
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    luotu = models.DateTimeField("Luotu", auto_now_add=True)
    muokattu = models.DateTimeField("Muokattu", auto_now=True)

    class Meta:
        db_table = "kommentit"
        verbose_name = "Kommentti"
        verbose_name_plural = "Kommentit"
        ordering = ["-luotu", "-kommentti_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kayttaja", "kirja"],
                name="uniikki_kayttaja_kirja_arvostelu",
            )
        ]

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
        if self.laina_alku and self.laina_loppu and self.laina_alku > self.laina_loppu:
            raise ValidationError(
                {"laina_loppu": "Lainan lopetusajan täytyy olla alkamisajan jälkeen."}
            )

    def __str__(self) -> str:
        return f"{self.kirja.nimi} -> {self.kayttaja.username}"
