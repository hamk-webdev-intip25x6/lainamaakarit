"""
Idempotent database population for PR5:
- Ensures sample books are present (without overwriting existing DB rows)
- Adds 5 invented Finnish users (if not already present)
- Adds 2 active book loans on different books (if not already present)
- Adds at least 3 Finnish reviews for every book (skipping any existing user+book pair)
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from .crypto import encrypt_pdf_file, is_encrypted_file
from .models import Kirja, Kommentti, Laina
from .services import (
    LoanError,
    SAMPLE_BOOKS,
    get_encrypted_dir,
    get_loans_dir,
    luo_laina,
    synkronoi_muokattu_laina,
)
from django.conf import settings
from pathlib import Path

User = get_user_model()

FINNISH_USERS = [
    {
        "username": "aino_korhonen",
        "first_name": "Aino",
        "last_name": "Korhonen",
        "email": "aino.korhonen@esimerkki.fi",
        "password": "AinoKirjasto2026!",
        "is_staff": True,
    },
    {
        "username": "mikko_virtanen",
        "first_name": "Mikko",
        "last_name": "Virtanen",
        "email": "mikko.virtanen@esimerkki.fi",
        "password": "MikkoLukija2026!",
        "is_staff": False,
    },
    {
        "username": "sari_makinen",
        "first_name": "Sari",
        "last_name": "Mäkinen",
        "email": "sari.makinen@esimerkki.fi",
        "password": "SariLukija2026!",
        "is_staff": False,
    },
    {
        "username": "pekka_nieminen",
        "first_name": "Pekka",
        "last_name": "Nieminen",
        "email": "pekka.nieminen@esimerkki.fi",
        "password": "PekkaLukija2026!",
        "is_staff": False,
    },
    {
        "username": "laura_heikkinen",
        "first_name": "Laura",
        "last_name": "Heikkinen",
        "email": "laura.heikkinen@esimerkki.fi",
        "password": "LauraLukija2026!",
        "is_staff": False,
    },
]

# Tailored Finnish review templates per book encrypted_filename, plus fallback templates
BOOK_REVIEWS_BY_FILE = {
    "atomic_habits.pdf.enc": [
        ("aino_korhonen", 5, "Selkeä ja käytännönläheinen kirja pienten arkirutiinien merkityksestä. Helppo lukea myös puhelimella."),
        ("mikko_virtanen", 5, "Yksi parhaista elämänhallintakirjoista. Kahden minuutin sääntö jäi heti omaan käyttöön!"),
        ("sari_makinen", 4, "Hyviä esimerkkejä ja sujuvaa tekstiä, vaikka loppupuolella on hieman toistoa."),
        ("pekka_nieminen", 5, "Erittäin motivoiva teos, suosittelen kaikille opiskelijoille."),
    ],
    "bd_chaurasia_general_anatomy.pdf.enc": [
        ("aino_korhonen", 4, "Kattava anatomian perusteos selkeillä kaaviokuvilla ja jäsennyksellä."),
        ("sari_makinen", 5, "Loistava lähdeteos opiskeluun. Käsitteet selitetään johdonmukaisesti alusta alkaen."),
        ("laura_heikkinen", 4, "Tiivis ja asiantunteva käsikirja, toimii hyvin hakuteoksena."),
    ],
    "the_48_laws_of_power.pdf.enc": [
        ("mikko_virtanen", 4, "Kiehtovia historiallisia tarinoita vallankäytöstä ja strategiasta eri aikakausilta."),
        ("pekka_nieminen", 3, "Mielenkiintoinen näkökulma historiaan, vaikka osa säännöistä on tarkoituksellisen kärjistettyjä."),
        ("laura_heikkinen", 4, "Runsaasti historiallisia anekdootteja, joita on helppo lukea luku kerrallaan."),
    ],
    "the_lean_startup.pdf.enc": [
        ("aino_korhonen", 5, "Erinomainen opas kokeilevaan tuotekehitykseen ja asiakaspalautteen hyödyntämiseen."),
        ("mikko_virtanen", 5, "Jokaisen ohjelmistokehittäjän ja yrittäjän kannattaa lukea tämä teos."),
        ("pekka_nieminen", 4, "Hyviä käytännön oppeja MVP-ajattelusta ja nopeasta oppimisesta."),
    ],
    "australian_commonwealth.pdf.enc": [
        ("aino_korhonen", 4, "Kiintoisa vanha historiateos Australian ja Uuden-Seelannin varhaisvaiheista."),
        ("sari_makinen", 3, "Aikansa näkökulmasta kirjoitettu perusteellinen katsaus, skannauslaatu on selkeä."),
        ("laura_heikkinen", 4, "Hyvä historiallinen lähdeteos 1800-luvun lopun tutkimukseen."),
    ],
    "decameron_boccaccio.pdf.enc": [
        ("aino_korhonen", 5, "Ajaton eurooppalaisen kirjallisuuden klassikko täynnä eläväisiä kertomuksia."),
        ("sari_makinen", 5, "Kehyskertomus ja sadan novellin rakenne toimivat edelleen upeasti."),
        ("pekka_nieminen", 4, "Renessanssin huumoria ja ihmiskuvausta parhaimmillaan."),
    ],
    "jk_paasikiven_paivakirjat.pdf.enc": [
        ("aino_korhonen", 5, "Ainutlaatuinen ja korvaamaton suomalaisen poliittisen historian aikalaislähde."),
        ("mikko_virtanen", 5, "Suomen sodanjälkeiset vuodet avautuvat suorien päiväkirjamerkintöjen kautta vaikuttavasti."),
        ("pekka_nieminen", 5, "Erittäin mielenkiintoista luettavaa jokaiselle Suomen historiasta kiinnostuneelle."),
        ("laura_heikkinen", 4, "Laaja teos, mutta hakutoiminnon avulla yksittäiset vuodet löytyvät vaivattomasti."),
    ],
    "stories_of_paul.pdf.enc": [
        ("sari_makinen", 4, "Lyhyt ja helppolukuinen kertomuskokoelma, joka sopii nopeaan lukuhetkeen."),
        ("pekka_nieminen", 3, "Selkeästi taitettu vanha julkaisu, mukavan tiivis pituus."),
        ("laura_heikkinen", 4, "Hyvin säilynyt historiallinen painos ja miellyttävä typografia."),
    ],
    "tales_from_hakluyt.pdf.enc": [
        ("mikko_virtanen", 4, "Vauhdikkaita merenkulku- ja löytöretkikertomuksia vuosisatojen takaa."),
        ("pekka_nieminen", 5, "Seikkailullinen ja mukaansatempaava kokoelma historiallisia matkakuvauksia."),
        ("laura_heikkinen", 4, "Hienosti toimitettu valikoima Richard Hakluytin alkuperäisistä teksteistä."),
    ],
    "world_revival_songs_and_hymns.pdf.enc": [
        ("aino_korhonen", 4, "Monipuolinen kokoelma perinteisiä lauluja ja neliäänisiä nuotteja."),
        ("sari_makinen", 4, "Nuotit näkyvät PDF-tiedostossa tarkasti myös tabletin näytöllä."),
        ("laura_heikkinen", 3, "Kattava historiallinen laulukirja musiikin harrastajille."),
    ],
}

FALLBACK_REVIEWS = [
    ("aino_korhonen", 5, "Erittäin hyödyllinen ja sujuvasti luettava teos kirjaston kokoelmassa."),
    ("mikko_virtanen", 4, "Mielenkiintoinen sisältö ja selkeä PDF-ulkoasu mobiililaitteella."),
    ("sari_makinen", 4, "Hyvä lukukokemus, suosittelen tutustumaan tähän kirjaan."),
]

SEED_LOANS = [
    ("mikko_virtanen", "the_lean_startup.pdf.enc"),
    ("sari_makinen", "stories_of_paul.pdf.enc"),
]


def populoi_tietokanta() -> dict[str, int]:
    """
    Populate the database idempotently without duplicating or overwriting existing records.

    Returns a summary dict with counts of newly created books, users, loans, and reviews.
    """
    source_dir = Path(getattr(settings, "PDF_SOURCE_DIR", settings.BASE_DIR / "pdf"))
    enc_dir = get_encrypted_dir()
    get_loans_dir()

    luodut_kirjat = 0
    for item in SAMPLE_BOOKS:
        src_path = source_dir / item["source_filename"]
        enc_path = enc_dir / item["encrypted_filename"]
        if src_path.is_file() and not is_encrypted_file(enc_path):
            encrypt_pdf_file(src_path, enc_path)

        if not Kirja.objects.filter(salattu_tiedosto=item["encrypted_filename"]).exists():
            if not Kirja.objects.filter(nimi=item["nimi"]).exists():
                Kirja.objects.create(
                    nimi=item["nimi"],
                    tekija=item["tekija"],
                    tehty=item["tehty"],
                    kategoria=item["kategoria"],
                    kieli=item["kieli"],
                    avainsana=item["avainsana"],
                    kuvaus=item["kuvaus"],
                    salattu_tiedosto=item["encrypted_filename"],
                )
                luodut_kirjat += 1

    # 1. Add 5 invented Finnish users (only if username is not already in DB)
    luodut_kayttajat = 0
    kayttaja_map = {}
    for u_data in FINNISH_USERS:
        olemassa = User.objects.filter(username=u_data["username"]).first()
        if olemassa is None:
            olemassa = User(
                username=u_data["username"],
                first_name=u_data["first_name"],
                last_name=u_data["last_name"],
                email=u_data["email"],
                is_staff=u_data["is_staff"],
            )
            olemassa.set_password(u_data["password"])
            olemassa.save()
            luodut_kayttajat += 1
        kayttaja_map[u_data["username"]] = olemassa

    # 2. Add 1–2 different book loans (only if not already present in DB)
    luodut_lainat = 0
    for username, enc_filename in SEED_LOANS:
        kayttaja = kayttaja_map.get(username)
        kirja = Kirja.objects.filter(salattu_tiedosto=enc_filename).first()
        if kayttaja is None or kirja is None:
            continue

        olemassa_laina = Laina.objects.filter(kirja=kirja, kayttaja=kayttaja).first()
        if olemassa_laina is not None:
            # Ensure temporary PDF exists if this existing loan is still active
            if olemassa_laina.on_voimassa:
                synkronoi_muokattu_laina(olemassa_laina)
            continue

        if kirja.aktiivinen_laina() is not None:
            continue

        try:
            luo_laina(kayttaja, kirja)
            luodut_lainat += 1
        except LoanError:
            # Fallback if encrypted binary is not present in minimal test setups
            now = timezone.now()
            Laina.objects.create(
                kirja=kirja,
                kayttaja=kayttaja,
                laina_alku=now,
                laina_loppu=now + timedelta(days=3),
            )
            luodut_lainat += 1

    # 3. Add at least 3 reviews for every book (never adding if already present)
    luodut_kommentit = 0
    now = timezone.now()
    for kirja_idx, kirja in enumerate(Kirja.objects.all().order_by("kirja_id")):
        suunnitellut = BOOK_REVIEWS_BY_FILE.get(kirja.salattu_tiedosto, FALLBACK_REVIEWS)
        for rev_idx, (username, arvosana, teksti) in enumerate(suunnitellut):
            kayttaja = kayttaja_map[username]
            if Kommentti.objects.filter(kirja=kirja, kayttaja=kayttaja).exists():
                continue

            kommentti = Kommentti.objects.create(
                kirja=kirja,
                kayttaja=kayttaja,
                arvosana=arvosana,
                teksti=teksti,
            )
            # Spread creation timestamps across recent days for realistic comment feed filtering
            paivia_sitten = (kirja_idx + rev_idx * 2) % 10
            luonti_aika = now - timedelta(days=paivia_sitten, hours=rev_idx * 3)
            Kommentti.objects.filter(pk=kommentti.pk).update(
                luotu=luonti_aika,
                muokattu=luonti_aika,
            )
            luodut_kommentit += 1

    return {
        "kirjat": luodut_kirjat,
        "kayttajat": luodut_kayttajat,
        "lainat": luodut_lainat,
        "kommentit": luodut_kommentit,
    }
