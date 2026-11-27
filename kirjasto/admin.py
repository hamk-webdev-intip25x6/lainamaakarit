from django.contrib import admin

from .models import Kirja, Kommentti, Laina


@admin.register(Kirja)
class KirjaAdmin(admin.ModelAdmin):
    list_display = ("kirja_id", "nimi", "tekija", "tehty", "kategoria", "kieli", "salattu_tiedosto")
    list_filter = ("kategoria", "kieli")
    search_fields = ("nimi", "tekija", "avainsana", "kategoria", "kuvaus")


@admin.register(Kommentti)
class KommenttiAdmin(admin.ModelAdmin):
    list_display = ("kommentti_id", "kirja", "kayttaja", "arvosana")
    list_filter = ("arvosana",)
    search_fields = ("teksti", "kirja__nimi", "kayttaja__username")


@admin.register(Laina)
class LainaAdmin(admin.ModelAdmin):
    list_display = ("laina_id", "kirja", "kayttaja", "laina_alku", "laina_loppu", "valiaikainen_tiedosto")
    search_fields = ("kirja__nimi", "kayttaja__username", "laina_tunniste")
