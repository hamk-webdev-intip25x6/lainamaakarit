"""
Library services: sample book catalog, encrypted PDF storage, and loan lifecycle.
"""

from datetime import timedelta
from pathlib import Path
import secrets
import shutil
import threading

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .crypto import decrypt_pdf_file, encrypt_pdf_file, is_encrypted_file
from .models import Kirja, Laina


class LoanError(Exception):
    """Raised when a book cannot be loaned."""


SAMPLE_BOOKS = [
    {
        "source_filename": "Atomic habits ( PDFDrive ).pdf",
        "encrypted_filename": "atomic_habits.pdf.enc",
        "nimi": "Atomic Habits",
        "tekija": "James Clear",
        "tehty": 2018,
        "kategoria": "Elämänhallinta",
        "kieli": "Englanti",
        "avainsana": "tavat, tuottavuus, elämänhallinta, rutiinit",
        "kuvaus": (
            "Käytännönläheinen opas hyvien tapojen rakentamiseen ja huonojen tapojen "
            "karsimiseen pienten päivittäisten muutosten avulla."
        ),
    },
    {
        "source_filename": "BD Chaurasia_s Handbook of General Anatomy, 4th Edition.pdf",
        "encrypted_filename": "bd_chaurasia_general_anatomy.pdf.enc",
        "nimi": "BD Chaurasia's Handbook of General Anatomy (4th Edition)",
        "tekija": "B. D. Chaurasia, Krishna Garg",
        "tehty": 2009,
        "kategoria": "Lääketiede",
        "kieli": "Englanti",
        "avainsana": "anatomia, lääketiede, ihmiskeho, oppikirja",
        "kuvaus": "Kattava yleisanatomian käsikirja lääketieteen ja terveystieteiden opiskelijoille.",
    },
    {
        "source_filename": "The 48 Laws Of Power ( PDFDrive.com ).pdf",
        "encrypted_filename": "the_48_laws_of_power.pdf.enc",
        "nimi": "The 48 Laws of Power",
        "tekija": "Robert Greene",
        "tehty": 1998,
        "kategoria": "Yhteiskunta ja psykologia",
        "kieli": "Englanti",
        "avainsana": "valta, strategia, psykologia, historia",
        "kuvaus": "Historiallisiin esimerkkeihin pohjautuva teos vallankäytön dynamiikasta ja strategiasta.",
    },
    {
        "source_filename": "The Lean Startup - Erick Ries.pdf",
        "encrypted_filename": "the_lean_startup.pdf.enc",
        "nimi": "The Lean Startup",
        "tekija": "Eric Ries",
        "tehty": 2011,
        "kategoria": "Liiketoiminta",
        "kieli": "Englanti",
        "avainsana": "yrittäjyys, startup, tuotekehitys, innovaatio",
        "kuvaus": (
            "Ketterän kokeilun ja jatkuvan oppimisen menetelmä uusien tuotteiden "
            "ja kasvuyritysten kehittämiseen."
        ),
    },
    {
        "source_filename": "australiancommon0000treg.pdf",
        "encrypted_filename": "australian_commonwealth.pdf.enc",
        "nimi": "Australian Commonwealth",
        "tekija": "Greville Philipps Tregarthen",
        "tehty": 1893,
        "kategoria": "Historia",
        "kieli": "Englanti",
        "avainsana": "Australia, Uusi-Seelanti, historia, kansainyhteisö",
        "kuvaus": "Historiallinen katsaus Australian osavaltioiden, Tasmanian ja Uuden-Seelannin vaiheisiin.",
    },
    {
        "source_filename": "decameronofgiova0000vari_y9w4.pdf",
        "encrypted_filename": "decameron_boccaccio.pdf.enc",
        "nimi": "The Decameron of Giovanni Boccaccio",
        "tekija": "Giovanni Boccaccio",
        "tehty": 1353,
        "kategoria": "Klassikkokirjallisuus",
        "kieli": "Englanti",
        "avainsana": "klassikko, novellit, renessanssi, Italia",
        "kuvaus": (
            "Sadan novellin klassikkokokoelma, jossa kymmenen nuorta kertoo tarinoita "
            "Firenzen ulkopuolella."
        ),
    },
    {
        "source_filename": "jkpaasikivenpaiv0000paas.pdf",
        "encrypted_filename": "jk_paasikiven_paivakirjat.pdf.enc",
        "nimi": "J. K. Paasikiven päiväkirjat 1944–1956",
        "tekija": "Juho Kusti Paasikivi",
        "tehty": 1985,
        "kategoria": "Historia",
        "kieli": "Suomi",
        "avainsana": "Suomen historia, politiikka, päiväkirjat, presidentti",
        "kuvaus": "Tasavallan presidentti J. K. Paasikiven päiväkirjamerkinnät sodanjälkeisiltä vuosilta 1944–1956.",
    },
    {
        "source_filename": "storiesofpaul0000vari.pdf",
        "encrypted_filename": "stories_of_paul.pdf.enc",
        "nimi": "Stories of Paul",
        "tekija": "Various",
        "tehty": 1900,
        "kategoria": "Uskonto ja historia",
        "kieli": "Englanti",
        "avainsana": "kertomukset, uskonto, apostoli Paavali, historia",
        "kuvaus": "Kuvitettu kertomuskokoelma Paavalin matkoista ja elämänvaiheista.",
    },
    {
        "source_filename": "talesfromhakluyt0000fran.pdf",
        "encrypted_filename": "tales_from_hakluyt.pdf.enc",
        "nimi": "Tales from Hakluyt",
        "tekija": "Frank Elias",
        "tehty": 1909,
        "kategoria": "Historia ja matkakertomukset",
        "kieli": "Englanti",
        "avainsana": "löytöretket, merenkulku, seikkailu, Hakluyt",
        "kuvaus": (
            "Kertomuksia varhaisista englantilaisista merenkulkijoista ja löytöretkeilijöistä "
            "Richard Hakluytin kokoelmien pohjalta."
        ),
    },
    {
        "source_filename": "worldrevivalsong0000welm.pdf",
        "encrypted_filename": "world_revival_songs_and_hymns.pdf.enc",
        "nimi": "The World Revival Songs and Hymns",
        "tekija": "W. Elmer Bailey",
        "tehty": 1915,
        "kategoria": "Musiikki",
        "kieli": "Englanti",
        "avainsana": "musiikki, hengelliset laulut, virret, nuotit",
        "kuvaus": "Kokoelma hengellisiä lauluja, hymnejä ja nuotteja yhteislauluun.",
    },
]


def get_library_root() -> Path:
    return Path(settings.LIBRARY_ROOT)


def get_encrypted_dir() -> Path:
    path = get_library_root() / "encrypted"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_loans_dir() -> Path:
    path = get_library_root() / "loans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_join(base_dir: Path, relative_path: str) -> Path:
    base_resolved = base_dir.resolve()
    candidate = (base_resolved / relative_path).resolve()
    if candidate == base_resolved or not candidate.is_relative_to(base_resolved):
        raise ValueError("Virheellinen tiedostopolku.")
    return candidate


def hae_laina_aika() -> timedelta:
    """Return configured loan duration: seconds (test), minutes (manual), or 3 days (default)."""
    override_seconds = getattr(settings, "LOAN_DURATION_SECONDS", None)
    if override_seconds is not None:
        return timedelta(seconds=float(override_seconds))

    mode = str(getattr(settings, "LOAN_MODE", "default")).lower()
    if mode == "test":
        return timedelta(seconds=2)
    if mode == "manual":
        return timedelta(minutes=3)
    return timedelta(days=3)


def hae_laina_ajan_kuvaus() -> str:
    """Human-readable Finnish description of the active loan duration."""
    kesto = hae_laina_aika()
    sekunnit = int(kesto.total_seconds())
    if sekunnit < 60:
        return f"{sekunnit} sekuntia"
    if sekunnit < 3600:
        minuutit = sekunnit // 60
        return f"{minuutit} minuuttia"
    if sekunnit < 86400:
        tunnit = sekunnit // 3600
        return f"{tunnit} tuntia"
    paivat = sekunnit // 86400
    return f"{paivat} vuorokautta"


def _poista_polku_turvallisesti(tiedosto_polku: Path, loans_dir: Path) -> None:
    try:
        if tiedosto_polku.is_file():
            tiedosto_polku.unlink(missing_ok=True)
        parent = tiedosto_polku.parent
        loans_resolved = loans_dir.resolve()
        if parent != loans_resolved and parent.is_relative_to(loans_resolved):
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass


def _ajasta_tiedoston_poisto(tiedosto_polku: Path, loans_dir: Path, viive_sekuntia: float) -> None:
    """Schedule background filesystem removal of the temporary decrypted copy upon expiry."""
    if viive_sekuntia <= 0:
        _poista_polku_turvallisesti(tiedosto_polku, loans_dir)
        return
    if viive_sekuntia > 3600:
        return
    timer = threading.Timer(
        viive_sekuntia,
        _poista_polku_turvallisesti,
        args=(tiedosto_polku, loans_dir),
    )
    timer.daemon = True
    timer.start()


def poista_lainan_tiedosto(laina: Laina) -> None:
    """Delete the temporary unencrypted PDF file associated with a loan."""
    loans_dir = get_loans_dir()
    if laina.valiaikainen_tiedosto:
        try:
            kohde = _safe_join(loans_dir, laina.valiaikainen_tiedosto)
            _poista_polku_turvallisesti(kohde, loans_dir)
        except ValueError:
            pass
        laina.valiaikainen_tiedosto = ""
        if laina.pk:
            laina.save(update_fields=["valiaikainen_tiedosto"])


def siivoa_vanhentuneet_lainat() -> int:
    """Delete temporary unencrypted PDF files for all expired loans and sweep orphans."""
    now = timezone.now()
    loans_dir = get_loans_dir()
    poistetut = 0

    vanhentuneet = Laina.objects.exclude(valiaikainen_tiedosto="").filter(
        laina_loppu__lte=now
    )
    for laina in vanhentuneet:
        poista_lainan_tiedosto(laina)
        poistetut += 1

    aktiiviset_polut = set()
    aktiiviset_kansiot = set()
    for rel_path in (
        Laina.objects.filter(laina_loppu__gt=now)
        .exclude(valiaikainen_tiedosto="")
        .values_list("valiaikainen_tiedosto", flat=True)
    ):
        try:
            resolved = _safe_join(loans_dir, rel_path)
            aktiiviset_polut.add(resolved)
            aktiiviset_kansiot.add(resolved.parent)
        except ValueError:
            continue

    for entry in loans_dir.iterdir():
        if entry.name == ".gitkeep":
            continue
        resolved_entry = entry.resolve()
        if entry.is_dir() and resolved_entry not in aktiiviset_kansiot:
            shutil.rmtree(resolved_entry, ignore_errors=True)
        elif entry.is_file() and resolved_entry not in aktiiviset_polut:
            entry.unlink(missing_ok=True)

    return poistetut


def luo_laina(kayttaja, kirja: Kirja) -> Laina:
    """Decrypt a temporary copy of kirja and create an active Laina for kayttaja."""
    siivoa_vanhentuneet_lainat()

    if not kirja.listattu:
        raise LoanError("Kirja on poistettu valikoimasta eikä ole lainattavissa.")

    if not kirja.salattu_tiedosto:
        raise LoanError("Tälle kirjalle ei ole määritetty salattua PDF-tiedostoa.")

    enc_dir = get_encrypted_dir()
    loans_dir = get_loans_dir()
    enc_path = _safe_join(enc_dir, kirja.salattu_tiedosto)
    if not enc_path.is_file():
        _varmista_salattu_esimerkkikirja(kirja.salattu_tiedosto)
    if not enc_path.is_file():
        raise LoanError("Kirjan salattua PDF-tiedostoa ei löytynyt kirjastokansiosta.")

    now = timezone.now()
    kesto = hae_laina_aika()

    with transaction.atomic():
        aktiivinen = (
            Laina.objects.select_for_update()
            .filter(kirja=kirja, laina_loppu__gt=now)
            .first()
        )
        if aktiivinen is not None:
            if aktiivinen.kayttaja_id == kayttaja.pk:
                raise LoanError("Sinulla on jo voimassa oleva laina tästä kirjasta.")
            raise LoanError("Kirja on tällä hetkellä lainassa toisella käyttäjällä.")

        token = secrets.token_urlsafe(24)
        safe_slug = slugify(kirja.nimi) or f"kirja-{kirja.kirja_id}"
        rel_temp = f"{token}/{safe_slug}.pdf"
        dest_path = _safe_join(loans_dir, rel_temp)

        decrypt_pdf_file(enc_path, dest_path)

        laina = Laina.objects.create(
            kirja=kirja,
            kayttaja=kayttaja,
            laina_alku=now,
            laina_loppu=now + kesto,
            laina_tunniste=token,
            valiaikainen_tiedosto=rel_temp,
        )

    _ajasta_tiedoston_poisto(dest_path, loans_dir, kesto.total_seconds())
    return laina


def lopeta_laina(laina: Laina) -> Laina:
    """End an active loan early and delete its temporary unencrypted PDF file."""
    now = timezone.now()
    poista_lainan_tiedosto(laina)
    if laina.laina_loppu > now:
        laina.laina_loppu = now
        laina.save(update_fields=["laina_loppu"])
    return laina


def synkronoi_muokattu_laina(laina: Laina, vanha_kirja_id: int | None = None) -> Laina:
    """
    Synchronize temporary PDF file state after an admin modifies a loan in Huolto.
    Deletes the temporary copy if expired; provisions a fresh copy if active and missing.
    """
    now = timezone.now()
    if laina.laina_loppu <= now:
        poista_lainan_tiedosto(laina)
        return laina

    loans_dir = get_loans_dir()
    kirja_vaihtui = vanha_kirja_id is not None and vanha_kirja_id != laina.kirja_id
    if kirja_vaihtui and laina.valiaikainen_tiedosto:
        poista_lainan_tiedosto(laina)

    tarvitsee_kopion = True
    if laina.valiaikainen_tiedosto:
        try:
            polku = _safe_join(loans_dir, laina.valiaikainen_tiedosto)
            if polku.is_file():
                tarvitsee_kopion = False
                viive = (laina.laina_loppu - now).total_seconds()
                _ajasta_tiedoston_poisto(polku, loans_dir, viive)
        except ValueError:
            tarvitsee_kopion = True

    if tarvitsee_kopion and laina.kirja.salattu_tiedosto:
        enc_path = _safe_join(get_encrypted_dir(), laina.kirja.salattu_tiedosto)
        if not enc_path.is_file():
            _varmista_salattu_esimerkkikirja(laina.kirja.salattu_tiedosto)
        if enc_path.is_file():
            token = laina.laina_tunniste or secrets.token_urlsafe(24)
            safe_slug = slugify(laina.kirja.nimi) or f"kirja-{laina.kirja_id}"
            rel_temp = f"{token}/{safe_slug}.pdf"
            dest_path = _safe_join(loans_dir, rel_temp)
            decrypt_pdf_file(enc_path, dest_path)
            laina.laina_tunniste = token
            laina.valiaikainen_tiedosto = rel_temp
            laina.save(update_fields=["laina_tunniste", "valiaikainen_tiedosto"])
            viive = (laina.laina_loppu - now).total_seconds()
            _ajasta_tiedoston_poisto(dest_path, loans_dir, viive)

    return laina


def hae_lainan_pdf_polku(laina: Laina) -> Path | None:
    """Return the path to the temporary unencrypted PDF if the loan is still active."""
    if not laina.on_voimassa:
        poista_lainan_tiedosto(laina)
        return None
    if not laina.valiaikainen_tiedosto:
        return None
    try:
        polku = _safe_join(get_loans_dir(), laina.valiaikainen_tiedosto)
    except ValueError:
        return None
    if not polku.is_file():
        return None
    return polku


def _varmista_salattu_esimerkkikirja(encrypted_filename: str) -> bool:
    source_dir = Path(getattr(settings, "PDF_SOURCE_DIR", settings.BASE_DIR / "pdf"))
    enc_dir = get_encrypted_dir()
    for entry in SAMPLE_BOOKS:
        if entry["encrypted_filename"] == encrypted_filename:
            src = source_dir / entry["source_filename"]
            dst = enc_dir / encrypted_filename
            if src.is_file() and not is_encrypted_file(dst):
                encrypt_pdf_file(src, dst)
                return True
    return False


def synkronoi_esimerkkikirjat(pakota_uudelleensalaus: bool = False) -> tuple[int, int]:
    """
    Encrypt sample PDFs from PDF_SOURCE_DIR into library/encrypted/ and
    populate or update Kirja database records idempotently.

    Returns (encrypted_count, created_books_count).
    """
    source_dir = Path(getattr(settings, "PDF_SOURCE_DIR", settings.BASE_DIR / "pdf"))
    enc_dir = get_encrypted_dir()
    get_loans_dir()

    salatut_lkm = 0
    luodut_kirjat_lkm = 0

    for item in SAMPLE_BOOKS:
        src_path = source_dir / item["source_filename"]
        enc_path = enc_dir / item["encrypted_filename"]

        if src_path.is_file() and (pakota_uudelleensalaus or not is_encrypted_file(enc_path)):
            encrypt_pdf_file(src_path, enc_path)
            salatut_lkm += 1

        defaults = {
            "nimi": item["nimi"],
            "tekija": item["tekija"],
            "tehty": item["tehty"],
            "kategoria": item["kategoria"],
            "kieli": item["kieli"],
            "avainsana": item["avainsana"],
            "kuvaus": item["kuvaus"],
        }
        _, created = Kirja.objects.update_or_create(
            salattu_tiedosto=item["encrypted_filename"],
            defaults=defaults,
        )
        if created:
            luodut_kirjat_lkm += 1

    return salatut_lkm, luodut_kirjat_lkm
