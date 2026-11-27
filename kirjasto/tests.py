from datetime import datetime, timedelta
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from django.conf import settings
from django.contrib.auth import get_user_model, views as auth_views
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from .crypto import EncryptionError, decrypt_pdf_file, encrypt_pdf_file, is_encrypted_file
from .forms import ArvosteluLomake, LainaMuokkausLomake, SignupForm
from .models import Kirja, Kommentti, Laina
from .populate import FINNISH_USERS, populoi_tietokanta
from .services import (
    LoanError,
    SAMPLE_BOOKS,
    _safe_join,
    hae_laina_aika,
    hae_laina_ajan_kuvaus,
    luo_laina,
    siivoa_vanhentuneet_lainat,
    synkronoi_esimerkkikirjat,
)

User = get_user_model()

MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"trailer<< /Root 1 0 R /Size 4 >>\nstartxref\n150\n%%EOF\n"
)


class DatabaseSchemaAndModelTests(TestCase):
    """Testit tietokantarakenteelle ja malleille (Kirja, Kommentti, Laina, User)."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="matti_meikäläinen",
            password="TurvallinenSalasana123!",
        )
        self.kirja = Kirja.objects.create(
            nimi="J. K. Paasikiven päiväkirjat 1944–1956",
            tekija="Juho Kusti Paasikivi",
            tehty=1985,
            avainsana="historia, politiikka, päiväkirjat",
        )

    def test_nelja_ydintaulua_ja_sarakkeet_loytyvat_tietokannasta(self) -> None:
        table_names = set(connection.introspection.table_names())
        self.assertIn("auth_user", table_names)
        self.assertIn("kirjat", table_names)
        self.assertIn("kommentit", table_names)
        self.assertIn("lainat", table_names)

        with connection.cursor() as cursor:
            kirjat_cols = {
                col.name
                for col in connection.introspection.get_table_description(cursor, "kirjat")
            }
            kommentit_cols = {
                col.name
                for col in connection.introspection.get_table_description(cursor, "kommentit")
            }
            lainat_cols = {
                col.name
                for col in connection.introspection.get_table_description(cursor, "lainat")
            }

        self.assertTrue(
            {
                "kirja_id",
                "nimi",
                "tekija",
                "tehty",
                "avainsana",
                "kategoria",
                "kieli",
                "kuvaus",
                "salattu_tiedosto",
                "listattu",
            }.issubset(kirjat_cols)
        )
        self.assertTrue(
            {
                "kommentti_id",
                "kayttaja_id",
                "kirja_id",
                "teksti",
                "arvosana",
                "luotu",
                "muokattu",
            }.issubset(kommentit_cols)
        )
        self.assertTrue(
            {
                "laina_id",
                "kirja_id",
                "kayttaja_id",
                "laina_alku",
                "laina_loppu",
                "laina_tunniste",
                "valiaikainen_tiedosto",
            }.issubset(lainat_cols)
        )

    def test_suomenkielinen_kirja_kommentti_ja_laina(self) -> None:
        kommentti = Kommentti.objects.create(
            kayttaja=self.user,
            kirja=self.kirja,
            teksti="Erittäin kattava ja hyödyllinen lähdeteos Suomen lähihistoriasta!",
            arvosana=5,
        )
        alku = timezone.now()
        loppu = alku + timedelta(days=3)
        laina = Laina.objects.create(
            kirja=self.kirja,
            kayttaja=self.user,
            laina_alku=alku,
            laina_loppu=loppu,
        )

        kommentti.full_clean()
        laina.full_clean()
        self.assertIsNotNone(self.kirja.kirja_id)
        self.assertIsNotNone(kommentti.kommentti_id)
        self.assertIsNotNone(kommentti.luotu)
        self.assertIsNotNone(laina.laina_id)
        self.assertIn("Paasikiven", str(self.kirja))
        self.assertIn("5/5", str(kommentti))
        self.assertIn("matti_meikäläinen", str(laina))

    def test_monikielinen_ja_erikoismerkkeja_sisaltava_data(self) -> None:
        """Testaa suomi, yksinkertaistettu kiina, arabia sekä sivustoa rikkovat merkit."""
        testitapaukset = [
            (
                "Ääkköset ja erikoismerkit: Yökyöpelin käsikirja",
                "Väinö Äyräpää",
                2024,
                "kääpiösnautseri, öljyvärimaalaus",
                "Tämä e-kirja toimii älypuhelimella moitteettomasti — suosittelen lämpimästi!",
            ),
            (
                "原子习惯：细微改变带来巨大成就的实证法则",
                "詹姆斯·克利尔",
                2018,
                "习惯, 自我提升, 心理学",
                "这本书非常实用，每天进步百分之一，长期坚持就会看到惊人的变化。强烈推荐阅读！",
            ),
            (
                "مقدمة ابن خلدون في التاريخ والاجتماع",
                "عبد الرحمن بن خلدون",
                1377,
                "تاريخ, فلسفة, اجتماع",
                "هذا الكتاب مفيد جداً وسهل القراءة على الهاتف المحمول. مكتبة رقمية رائعة ومريحة للاستخدام.",
            ),
            (
                "<script>alert('XSS')</script> & \"lainaus\" ' OR 1=1; DROP TABLE kirjat;--",
                "O'Connor & <img src=x onerror=alert(1)>",
                2026,
                "📚🔥👩‍💻 \u200b\u202eevil_rtl_override\u202c \\back\\slash",
                (
                    "PitkäKatkeamatonMerkkijono_" + ("ÄÖÅ漢字العربية" * 40)
                    + " </textarea></script><svg onload=alert(1)>"
                ),
            ),
        ]

        for nimi, tekija, tehty, avainsana, kommentti_teksti in testitapaukset:
            with self.subTest(nimi=nimi):
                kirja = Kirja.objects.create(
                    nimi=nimi,
                    tekija=tekija,
                    tehty=tehty,
                    avainsana=avainsana,
                )
                kommentti = Kommentti.objects.create(
                    kayttaja=self.user,
                    kirja=kirja,
                    teksti=kommentti_teksti,
                    arvosana=4,
                )
                kirja.refresh_from_db()
                kommentti.refresh_from_db()
                self.assertEqual(kirja.nimi, nimi)
                self.assertEqual(kirja.tekija, tekija)
                self.assertEqual(kommentti.teksti, kommentti_teksti)

    def test_kommentin_arvosanan_raja_arvot(self) -> None:
        for kelvollinen in (1, 3, 5):
            k = Kommentti(
                kayttaja=self.user,
                kirja=self.kirja,
                teksti="Kelvollinen arvosana.",
                arvosana=kelvollinen,
            )
            k.full_clean()

        for virheellinen in (-1, 0, 6, 100):
            with self.subTest(arvosana=virheellinen):
                k = Kommentti(
                    kayttaja=self.user,
                    kirja=self.kirja,
                    teksti="Virheellinen arvosana.",
                    arvosana=virheellinen,
                )
                with self.assertRaises(ValidationError):
                    k.full_clean()

    def test_lainan_aikajarjestys_validointi(self) -> None:
        alku = timezone.now()
        virheellinen_laina = Laina(
            kirja=self.kirja,
            kayttaja=self.user,
            laina_alku=alku,
            laina_loppu=alku - timedelta(seconds=10),
        )
        with self.assertRaises(ValidationError):
            virheellinen_laina.full_clean()


class AuthAndFormsTests(TestCase):
    """Testit rekisteröitymislomakkeelle, salasanan salaukselle, kirjautumiselle ja uloskirjautumiselle."""

    def test_login_ja_logout_kayttavat_djangon_vakionakymia(self) -> None:
        login_match = resolve(reverse("login"))
        logout_match = resolve(reverse("logout"))
        self.assertIs(login_match.func.view_class, auth_views.LoginView)
        self.assertIs(logout_match.func.view_class, auth_views.LogoutView)

    def test_signup_lomake_sisaltaa_vain_kayttajanimen_ja_salasanan_seka_salaa_salasanan(self) -> None:
        raaka_salasana = "KissaIstuuPuussa2026!"
        form = SignupForm(
            data={
                "username": "aino_lukija",
                "password": raaka_salasana,
            }
        )
        self.assertEqual(list(form.fields.keys()), ["username", "password"])
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertNotEqual(user.password, raaka_salasana)
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(user.check_password(raaka_salasana))

    def test_rekisteroityminen_monikielisilla_kayttajanimilla(self) -> None:
        kayttajanimet = ["Väinämöinen_99", "张伟_读者", "أحمد_القارئ"]
        for tunnus in kayttajanimet:
            with self.subTest(username=tunnus):
                self.client.logout()
                response = self.client.post(
                    reverse("signup"),
                    {"username": tunnus, "password": "VahvaSalasana987!"},
                    follow=True,
                )
                self.assertEqual(response.status_code, 200)
                luotu = User.objects.get(username=tunnus)
                self.assertTrue(luotu.check_password("VahvaSalasana987!"))
                self.assertContains(response, tunnus)

    def test_rekisteroityminen_estaa_duplikaatin_ja_heikon_salasanan(self) -> None:
        User.objects.create_user(username="pekka", password="HyvaSalasana123!")

        duplikaatti = SignupForm(data={"username": "PEKKA", "password": "ToinenHyvaSalasana123!"})
        self.assertFalse(duplikaatti.is_valid())
        self.assertIn("username", duplikaatti.errors)

        heikko = SignupForm(data={"username": "uusi_kayttaja", "password": "123"})
        self.assertFalse(heikko.is_valid())
        self.assertIn("password", heikko.errors)

        tyhja = SignupForm(data={"username": "   ", "password": "ToinenHyvaSalasana123!"})
        self.assertFalse(tyhja.is_valid())
        self.assertIn("username", tyhja.errors)

    def test_kirjautuminen_ja_uloskirjautuminen_toimivat(self) -> None:
        User.objects.create_user(username="liisa", password="LiisanSalasana456!")

        bad_resp = self.client.post(
            reverse("login"),
            {"username": "liisa", "password": "vaara"},
        )
        self.assertEqual(bad_resp.status_code, 200)
        self.assertFalse(bad_resp.wsgi_request.user.is_authenticated)

        ok_resp = self.client.post(
            reverse("login"),
            {"username": "liisa", "password": "LiisanSalasana456!"},
            follow=True,
        )
        self.assertRedirects(ok_resp, reverse("home"))
        self.assertTrue(ok_resp.wsgi_request.user.is_authenticated)

        get_logout = self.client.get(reverse("logout"))
        self.assertEqual(get_logout.status_code, 405)

        post_logout = self.client.post(reverse("logout"), follow=True)
        self.assertRedirects(post_logout, reverse("home"))
        self.assertFalse(post_logout.wsgi_request.user.is_authenticated)


class TemplatesAndThemeTests(TestCase):
    """Testit etusivulle, base.html-perinnälle, responsiivisuudelle, teemoille ja XSS-suojaukselle."""

    def test_etusivu_kirjautumattomalle_nayttaa_login_ja_signup_linkit(self) -> None:
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "kirjasto/home.html")
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, f'href="{reverse("login")}"')
        self.assertContains(response, f'href="{reverse("signup")}"')
        self.assertNotContains(response, "Kirjaudu ulos")

    def test_etusivu_kirjautuneelle_nayttaa_kayttajan_ja_uloskirjautumispainikkeen(self) -> None:
        user = User.objects.create_user(username="kalle_lukija", password="KallenSalasana789!")
        self.client.force_login(user)

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kalle_lukija")
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, "Kirjaudu ulos")

    def test_xss_erikoismerkit_kayttajanimessa_pakataan_turvallisesti(self) -> None:
        xss_user = User.objects.create(username='<script>alert("xss")</script>')
        xss_user.set_password("TurvaSalasana123!")
        xss_user.save()
        self.client.force_login(xss_user)

        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script>alert("xss")</script>')
        self.assertContains(response, "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;")

    def test_kaikki_sivut_perivat_responsiivisen_base_templaten_ja_nelja_teemaa(self) -> None:
        kirja = Kirja.objects.create(
            nimi="Testikirja",
            tekija="Testitekijä",
            tehty=2025,
            avainsana="testi",
        )
        user = User.objects.create_user(username="teema_testaaja", password="TeemaSalasana123!")

        for url in (
            reverse("home"),
            reverse("login"),
            reverse("signup"),
            reverse("kirja_lista"),
            reverse("kirja_tiedot", args=[kirja.kirja_id]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "base.html")
                self.assertContains(response, 'name="viewport"')
                self.assertContains(response, 'width=device-width, initial-scale=1.0')
                self.assertContains(response, 'id="theme-select"')
                for mode in ("light", "dark", "high-light", "high-dark"):
                    self.assertContains(response, f'value="{mode}"')
                self.assertContains(response, "localStorage")

        self.client.force_login(user)
        omat_resp = self.client.get(reverse("omat_lainat"))
        self.assertEqual(omat_resp.status_code, 200)
        self.assertTemplateUsed(omat_resp, "base.html")

    def test_staattiset_css_ja_vanilla_js_tiedostot_loytyvat_ja_sisaltavat_teemat(self) -> None:
        css_path = finders.find("css/styles.css")
        js_path = finders.find("js/theme.js")
        self.assertIsNotNone(css_path)
        self.assertIsNotNone(js_path)

        with open(css_path, encoding="utf-8") as f:
            css_content = f.read()
        for selector in (
            '[data-theme="light"]',
            '[data-theme="dark"]',
            '[data-theme="high-light"]',
            '[data-theme="high-dark"]',
            ".review-body",
            "overflow-wrap: anywhere",
            "unicode-bidi: plaintext",
        ):
            self.assertIn(selector, css_content)

        with open(js_path, encoding="utf-8") as f:
            js_content = f.read()
        self.assertIn("localStorage.setItem", js_content)
        self.assertIn("localStorage.getItem", js_content)
        for mode in ("light", "dark", "high-light", "high-dark"):
            self.assertIn(mode, js_content)


class PDFLibraryEncryptionTests(TestCase):
    """Testit PDF-tiedostojen salaukselle, eheystarkistukselle ja esimerkkikirjoille."""

    def test_pdf_salaus_purku_ja_muutoksen_havaitseminen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            alkuperainen = tmp / "testi.pdf"
            salattu = tmp / "encrypted" / "testi.pdf.enc"
            purettu = tmp / "loans" / "testi_purettu.pdf"

            alkuperainen.write_bytes(MINIMAL_PDF_BYTES)
            encrypt_pdf_file(alkuperainen, salattu, secret="salainen-avain-123")

            self.assertTrue(is_encrypted_file(salattu))
            salattu_data = salattu.read_bytes()
            self.assertNotIn(b"%PDF-", salattu_data)

            decrypt_pdf_file(salattu, purettu, secret="salainen-avain-123")
            self.assertEqual(purettu.read_bytes(), MINIMAL_PDF_BYTES)

            with self.assertRaises(EncryptionError):
                decrypt_pdf_file(salattu, tmp / "vaara.pdf", secret="vaara-avain")
            self.assertFalse((tmp / "vaara.pdf").exists())

            peukaloitu = tmp / "peukaloitu.pdf.enc"
            mutable = bytearray(salattu_data)
            mutable[30] ^= 0xFF
            peukaloitu.write_bytes(bytes(mutable))
            with self.assertRaises(EncryptionError):
                decrypt_pdf_file(peukaloitu, tmp / "ei_saa_syntya.pdf", secret="salainen-avain-123")
            self.assertFalse((tmp / "ei_saa_syntya.pdf").exists())

    def test_kymmenen_esimerkkikirjaa_synkronoituvat_ja_ovat_salattuja(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            pdf_src = tmp_root / "pdf"
            lib_dir = tmp_root / "library"
            pdf_src.mkdir()

            for item in SAMPLE_BOOKS:
                (pdf_src / item["source_filename"]).write_bytes(MINIMAL_PDF_BYTES)

            with override_settings(PDF_SOURCE_DIR=pdf_src, LIBRARY_ROOT=lib_dir):
                salatut1, luodut1 = synkronoi_esimerkkikirjat()
                self.assertEqual(salatut1, 10)
                self.assertEqual(luodut1, 10)
                self.assertEqual(Kirja.objects.count(), 10)

                salatut2, luodut2 = synkronoi_esimerkkikirjat()
                self.assertEqual(salatut2, 0)
                self.assertEqual(luodut2, 0)
                self.assertEqual(Kirja.objects.count(), 10)

                for item in SAMPLE_BOOKS:
                    enc_file = lib_dir / "encrypted" / item["encrypted_filename"]
                    self.assertTrue(is_encrypted_file(enc_file))
                    self.assertFalse(enc_file.read_bytes().startswith(b"%PDF"))


class LoanLifecycleAndSharingTests(TestCase):
    """Testit lainaamiselle, väliaikaiselle PDF-linkille, sekuntivanhenemiselle ja palautukselle."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.lib_root = self.tmp_path / "library"
        self.src_dir = self.tmp_path / "pdf"
        self.src_dir.mkdir()
        (self.lib_root / "encrypted").mkdir(parents=True)
        (self.lib_root / "loans").mkdir(parents=True)

        self.override = override_settings(
            LIBRARY_ROOT=self.lib_root,
            PDF_SOURCE_DIR=self.src_dir,
            LOAN_MODE="default",
            LOAN_DURATION_SECONDS=None,
        )
        self.override.enable()

        raaka_pdf = self.src_dir / "paasikivi.pdf"
        raaka_pdf.write_bytes(MINIMAL_PDF_BYTES)
        encrypt_pdf_file(raaka_pdf, self.lib_root / "encrypted" / "paasikivi.pdf.enc")

        self.kirja = Kirja.objects.create(
            nimi="Paasikiven päiväkirjat",
            tekija="Juho Kusti Paasikivi",
            tehty=1985,
            kategoria="Historia",
            kieli="Suomi",
            avainsana="historia, politiikka",
            kuvaus="Suomen lähihistorian merkittävä päiväkirjateos.",
            salattu_tiedosto="paasikivi.pdf.enc",
        )
        self.lainaaja = User.objects.create_user(
            username="lainaaja_aino",
            password="AinonSalasana123!",
        )
        self.toinen = User.objects.create_user(
            username="toinen_lukija",
            password="ToisenSalasana123!",
        )

    def tearDown(self) -> None:
        self.override.disable()
        self.tmpdir.cleanup()

    def test_laina_ajan_tilat_kolme_paivaa_minuutit_ja_sekunnit(self) -> None:
        with override_settings(LOAN_MODE="default", LOAN_DURATION_SECONDS=None):
            self.assertEqual(hae_laina_aika(), timedelta(days=3))
        with override_settings(LOAN_MODE="manual", LOAN_DURATION_SECONDS=None):
            self.assertEqual(hae_laina_aika(), timedelta(minutes=3))
        with override_settings(LOAN_MODE="test", LOAN_DURATION_SECONDS=None):
            self.assertEqual(hae_laina_aika(), timedelta(seconds=2))
        with override_settings(LOAN_DURATION_SECONDS=0.5):
            self.assertEqual(hae_laina_aika(), timedelta(seconds=0.5))

    def test_lainaus_luo_valiaikaisen_pdf_kopion_ja_palautus_poistaa_sen_heti(self) -> None:
        self.client.force_login(self.lainaaja)
        resp = self.client.post(
            reverse("lainaa_kirja", args=[self.kirja.kirja_id]),
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        laina = Laina.objects.get(kirja=self.kirja, kayttaja=self.lainaaja)
        temp_pdf = self.lib_root / "loans" / laina.valiaikainen_tiedosto
        self.assertTrue(temp_pdf.is_file())
        self.assertEqual(temp_pdf.read_bytes(), MINIMAL_PDF_BYTES)

        pdf_url = reverse("laina_pdf", args=[laina.laina_tunniste])
        pdf_resp = self.client.get(pdf_url)
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(pdf_resp.streaming_content), MINIMAL_PDF_BYTES)

        dl_resp = self.client.get(pdf_url + "?download=1")
        self.assertEqual(dl_resp.status_code, 200)
        self.assertIn("attachment", dl_resp["Content-Disposition"])
        dl_resp.close()

        self.client.force_login(self.toinen)
        self.assertEqual(self.client.get(pdf_url).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("lopeta_laina", args=[laina.laina_id])).status_code,
            403,
        )
        esto_resp = self.client.post(
            reverse("lainaa_kirja", args=[self.kirja.kirja_id]),
            follow=True,
        )
        self.assertContains(esto_resp, "lainassa toisella käyttäjällä")

        self.client.force_login(self.lainaaja)
        lopetus_resp = self.client.post(
            reverse("lopeta_laina", args=[laina.laina_id]),
            follow=True,
        )
        self.assertEqual(lopetus_resp.status_code, 200)
        self.assertFalse(temp_pdf.exists())
        self.assertEqual(self.client.get(pdf_url).status_code, 404)

        self.client.force_login(self.toinen)
        uusi_resp = self.client.post(
            reverse("lainaa_kirja", args=[self.kirja.kirja_id]),
            follow=True,
        )
        self.assertEqual(uusi_resp.status_code, 200)
        self.assertTrue(
            Laina.objects.filter(
                kirja=self.kirja,
                kayttaja=self.toinen,
                laina_loppu__gt=timezone.now(),
            ).exists()
        )

    @override_settings(LOAN_DURATION_SECONDS=0.35)
    def test_automaattinen_sekuntivanheneminen_poistaa_valiaikaisen_pdf_tiedoston(self) -> None:
        self.client.force_login(self.lainaaja)
        self.client.post(reverse("lainaa_kirja", args=[self.kirja.kirja_id]))

        laina = Laina.objects.get(kirja=self.kirja, kayttaja=self.lainaaja)
        temp_pdf = self.lib_root / "loans" / laina.valiaikainen_tiedosto
        pdf_url = reverse("laina_pdf", args=[laina.laina_tunniste])

        self.assertTrue(temp_pdf.is_file())
        first_get = self.client.get(pdf_url)
        self.assertEqual(first_get.status_code, 200)
        first_get.close()

        time.sleep(0.55)

        self.assertFalse(temp_pdf.exists())
        self.assertEqual(self.client.get(pdf_url).status_code, 404)


class LibrarySearchAndFrontendTests(TestCase):
    """Testit kirjaston haulle, suodatukselle sekä monikieliselle ja haastavalle syötteelle."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="hakija", password="HakijanSalasana123!")
        self.kirja_suomi = Kirja.objects.create(
            nimi="Ääkkösten käsikirja",
            tekija="Väinö Äyräpää",
            tehty=2021,
            kategoria="Kielitiede",
            kieli="Suomi",
            avainsana="suomen kieli, ääkköset",
            kuvaus="Suomenkielinen opas ääkkösten historiaan.",
        )
        self.kirja_kiina = Kirja.objects.create(
            nimi="三体：地球往事",
            tekija="刘慈欣",
            tehty=2008,
            kategoria="Tieteiskirjallisuus",
            kieli="Kiina",
            avainsana="科幻, 宇宙, 物理学",
            kuvaus="一部关于人类文明与外星文明首次接触的科幻小说。",
        )
        self.kirja_arabia = Kirja.objects.create(
            nimi="ألف ليلة وليلة",
            tekija="تراث عربي",
            tehty=1400,
            kategoria="Klassikkokirjallisuus",
            kieli="Arabia",
            avainsana="قصص, تراث, خيال",
            kuvaus="مجموعة من القصص والحكايات الشعبية الشرقية الرائعة.",
        )
        self.kirja_lainassa = Kirja.objects.create(
            nimi="Varattu erikoisteos",
            tekija="Matti Lainattu",
            tehty=2024,
            kategoria="Historia",
            kieli="Suomi",
            avainsana="varattu",
            kuvaus="Tämä kirja on parhaillaan lainassa.",
        )
        Laina.objects.create(
            kirja=self.kirja_lainassa,
            kayttaja=self.user,
            laina_alku=timezone.now(),
            laina_loppu=timezone.now() + timedelta(days=2),
        )

    def test_haku_suomeksi_kiinaksi_arabiaksi_ja_vuosiluvulla(self) -> None:
        url = reverse("kirja_lista")

        r_fi = self.client.get(url, {"q": "Äyräpää"})
        self.assertContains(r_fi, "Ääkkösten käsikirja")
        self.assertNotContains(r_fi, "三体：地球往事")

        r_zh = self.client.get(url, {"q": "科幻"})
        self.assertContains(r_zh, "三体：地球往事")
        self.assertNotContains(r_zh, "Ääkkösten käsikirja")

        r_ar = self.client.get(url, {"q": "ليلة"})
        self.assertContains(r_ar, "ألف ليلة وليلة")
        self.assertNotContains(r_ar, "Ääkkösten käsikirja")

        r_year = self.client.get(url, {"q": "2008"})
        self.assertContains(r_year, "三体：地球往事")
        self.assertNotContains(r_year, "Varattu erikoisteos")

    def test_vain_saatavilla_ja_kategoria_suodatus(self) -> None:
        url = reverse("kirja_lista")

        kaikki = self.client.get(url)
        self.assertContains(kaikki, "Varattu erikoisteos")
        self.assertContains(kaikki, "Ääkkösten käsikirja")

        vain_vapaat = self.client.get(url, {"vain_saatavilla": "1"})
        self.assertContains(vain_vapaat, "Ääkkösten käsikirja")
        self.assertNotContains(vain_vapaat, "Varattu erikoisteos")

        kat = self.client.get(url, {"kategoria": "Tieteiskirjallisuus"})
        self.assertContains(kat, "三体：地球往事")
        self.assertNotContains(kat, "Ääkkösten käsikirja")

    def test_rikovat_hakusanat_xss_ja_sqli_kestetaan_turvallisesti(self) -> None:
        hyokkays = "<script>alert('xss')</script>' OR 1=1; DROP TABLE kirjat;--"
        resp = self.client.get(reverse("kirja_lista"), {"q": hyokkays})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<script>alert('xss')</script>")
        self.assertEqual(Kirja.objects.count(), 4)


class BookReviewsAndRatingsTests(TestCase):
    """PR3-testit: kirjan arvostelut, muokkaus, uniikkius, tähtimediaani sekä kielisyötteet."""

    def setUp(self) -> None:
        self.kirja = Kirja.objects.create(
            nimi="The Lean Startup",
            tekija="Eric Ries",
            tehty=2011,
            kategoria="Liiketoiminta",
            kieli="Englanti",
            avainsana="startup, yrittäjyys",
            kuvaus="Ketterän tuotekehityksen perusteos.",
        )
        self.u1 = User.objects.create_user(username="lukija_1", password="SalasanaYksi123!")
        self.u2 = User.objects.create_user(username="lukija_2", password="SalasanaKaksi123!")
        self.u3 = User.objects.create_user(username="lukija_3", password="SalasanaKolme123!")
        self.u4 = User.objects.create_user(username="lukija_4", password="SalasanaNelja123!")

    def test_kayttaja_voi_arvostella_kirjan_vain_kerran_mutta_voi_muokata_sita(self) -> None:
        self.client.force_login(self.u1)
        arvostelu_url = reverse("tallenna_arvostelu", args=[self.kirja.kirja_id])

        # Ensimmäinen arvostelu luodaan
        r1 = self.client.post(
            arvostelu_url,
            {"arvosana": 3, "teksti": "Ensimmäinen lukukokemus oli kohtalainen."},
            follow=True,
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(Kommentti.objects.filter(kirja=self.kirja, kayttaja=self.u1).count(), 1)
        alkuperainen = Kommentti.objects.get(kirja=self.kirja, kayttaja=self.u1)
        self.assertEqual(alkuperainen.arvosana, 3)
        self.assertIsNotNone(alkuperainen.luotu)
        self.assertContains(r1, "Muokkaa arvosteluasi")
        self.assertContains(r1, "Päivitä arvostelu")

        # Toinen lähetys samalta käyttäjältä päivittää olemassa olevan arvostelun luomatta uutta
        r2 = self.client.post(
            arvostelu_url,
            {"arvosana": 5, "teksti": "Uudelleenluettuna erinomainen ja käytännöllinen kirja!"},
            follow=True,
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Kommentti.objects.filter(kirja=self.kirja, kayttaja=self.u1).count(), 1)
        paivitetty = Kommentti.objects.get(kirja=self.kirja, kayttaja=self.u1)
        self.assertEqual(paivitetty.kommentti_id, alkuperainen.kommentti_id)
        self.assertEqual(paivitetty.arvosana, 5)
        self.assertEqual(
            paivitetty.teksti,
            "Uudelleenluettuna erinomainen ja käytännöllinen kirja!",
        )

        # Myös tietokannan UniqueConstraint estää tupla-arvostelun suoraan ORM:n kautta
        with self.assertRaises(IntegrityError):
            Kommentti.objects.create(
                kayttaja=self.u1,
                kirja=self.kirja,
                arvosana=4,
                teksti="Suora duplikaattiyritys",
            )

    def test_mediaani_ja_keskiarvo_lasketaan_ja_naytetaan_lainaussivulla(self) -> None:
        sivu_url = reverse("kirja_tiedot", args=[self.kirja.kirja_id])

        # Ei arvosteluja aluksi
        self.assertIsNone(self.kirja.arvosana_mediaani())
        self.assertIsNone(self.kirja.arvosana_keskiarvo())
        tyhja_resp = self.client.get(sivu_url)
        self.assertContains(tyhja_resp, "Ei vielä arvosteluja")

        # Pariton määrä arvosanoja poikkeavalla arvolla: [1, 5, 5] -> mediaani 5.0, keskiarvo 3.7
        Kommentti.objects.create(kayttaja=self.u1, kirja=self.kirja, arvosana=1, teksti="Ei minun makuuni.")
        Kommentti.objects.create(kayttaja=self.u2, kirja=self.kirja, arvosana=5, teksti="Huipputeos!")
        Kommentti.objects.create(kayttaja=self.u3, kirja=self.kirja, arvosana=5, teksti="Suosittelen lämpimästi!")

        self.assertEqual(self.kirja.arvosana_mediaani(), 5.0)
        self.assertEqual(self.kirja.arvosana_keskiarvo(), 3.7)

        pariton_resp = self.client.get(sivu_url)
        self.assertContains(pariton_resp, "5,0 / 5")
        self.assertContains(pariton_resp, "mediaani: 5,0")
        self.assertContains(pariton_resp, "keskiarvo: 3,7")
        self.assertContains(pariton_resp, "3 arvostelua")

        # Parillinen määrä arvosanoja: [1, 4, 5, 5] -> mediaani 4.5, keskiarvo 3.8
        Kommentti.objects.create(kayttaja=self.u4, kirja=self.kirja, arvosana=4, teksti="Hyvä kirja.")
        self.assertEqual(self.kirja.arvosana_mediaani(), 4.5)
        self.assertEqual(self.kirja.arvosana_keskiarvo(), 3.8)

        parillinen_resp = self.client.get(sivu_url)
        self.assertContains(parillinen_resp, "4,5 / 5")
        self.assertContains(parillinen_resp, "mediaani: 4,5")

    def test_arvostelulomakkeen_validointi_ja_pituusrajoite(self) -> None:
        # Kirjautumaton ohjataan kirjautumissivulle
        anon = self.client.post(
            reverse("tallenna_arvostelu", args=[self.kirja.kirja_id]),
            {"arvosana": 5, "teksti": "Anonyymi yritys"},
        )
        self.assertEqual(anon.status_code, 302)
        self.assertIn(reverse("login"), anon["Location"])

        self.client.force_login(self.u1)
        url = reverse("tallenna_arvostelu", args=[self.kirja.kirja_id])

        # Virheelliset tähtiarvosanat (0 ja 6) hylätään
        for virhe_tahti in (0, 6, -1):
            resp = self.client.post(url, {"arvosana": virhe_tahti, "teksti": "Kelvollinen teksti"})
            self.assertEqual(resp.status_code, 400)
        self.assertEqual(Kommentti.objects.count(), 0)

        # Tyhjä teksti hylätään
        tyhja_resp = self.client.post(url, {"arvosana": 4, "teksti": "   \n\t  "})
        self.assertEqual(tyhja_resp.status_code, 400)

        # Ylipitkä arvostelu (> 2000 merkkiä) hylätään sivun hajoamisen estämiseksi
        ylipitka = ArvosteluLomake(
            data={"arvosana": 4, "teksti": "Ä" * (Kommentti.MAX_TEKSTI_PITUUS + 1)}
        )
        self.assertFalse(ylipitka.is_valid())
        self.assertIn("teksti", ylipitka.errors)

    def test_monikieliset_ja_sivua_rikkovat_arvostelut_renderoityvat_turvallisesti(self) -> None:
        arvostelut = [
            (
                self.u1,
                5,
                "Erinomainen suomenkielinen lukukokemus! Ääkköset (ä, ö, å) ja pitkät yhdyssanat toimivat.",
            ),
            (
                self.u2,
                5,
                "这本书内容充实、结构清晰，非常适合在移动设备上阅读，五星推荐给所有读者！",
            ),
            (
                self.u3,
                4,
                "مراجعة ممتازة: الكتاب واضح وسهل التصفح، ونظام التقييم بالنجوم يعمل بشكل رائع!",
            ),
            (
                self.u4,
                2,
                (
                    '<script>alert("xss")</script></textarea><div style="width:99999px">'
                    + ("KatkeamatonPitkäMerkkijonoIlmanVälilyöntejä" * 20)
                    + " \u202eRTL_OVERRIDE\u202c 📚🔥"
                ),
            ),
        ]

        url = reverse("tallenna_arvostelu", args=[self.kirja.kirja_id])
        for kayttaja, arvosana, teksti in arvostelut:
            self.client.force_login(kayttaja)
            resp = self.client.post(url, {"arvosana": arvosana, "teksti": teksti}, follow=True)
            self.assertEqual(resp.status_code, 200)

        sivu = self.client.get(reverse("kirja_tiedot", args=[self.kirja.kirja_id]))
        self.assertEqual(sivu.status_code, 200)
        self.assertContains(sivu, "非常适合在移动设备上阅读")
        self.assertContains(sivu, "نظام التقييم بالنجوم")
        self.assertNotContains(sivu, '<script>alert("xss")</script>')
        self.assertContains(sivu, "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;")


class HuoltoAdminPanelTests(TestCase):
    """PR4-testit: Huolto-ylläpitopaneeli, lainojen muokkaus/päättäminen, piilotus ja kommenttisyöte."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)
        self.lib_root = self.tmp_path / "library"
        self.src_dir = self.tmp_path / "pdf"
        self.src_dir.mkdir()
        (self.lib_root / "encrypted").mkdir(parents=True)
        (self.lib_root / "loans").mkdir(parents=True)

        self.override = override_settings(
            LIBRARY_ROOT=self.lib_root,
            PDF_SOURCE_DIR=self.src_dir,
            LOAN_MODE="default",
            LOAN_DURATION_SECONDS=None,
        )
        self.override.enable()

        raaka_pdf = self.src_dir / "huolto_kirja.pdf"
        raaka_pdf.write_bytes(MINIMAL_PDF_BYTES)
        encrypt_pdf_file(raaka_pdf, self.lib_root / "encrypted" / "huolto_kirja.pdf.enc")

        self.admin = User.objects.create_user(
            username="yllapitaja_mikko",
            password="AdminSalasana123!",
            is_staff=True,
        )
        self.tavallinen = User.objects.create_user(
            username="perus_lukija",
            password="PerusSalasana123!",
        )
        self.toinen = User.objects.create_user(
            username="toinen_arvostelija",
            password="ToinenSalasana123!",
        )
        self.kolmas = User.objects.create_user(
            username="kolmas_arvostelija",
            password="KolmasSalasana123!",
        )

        self.kirja = Kirja.objects.create(
            nimi="Huoltotestin käsikirja",
            tekija=" Maija Meikäläinen",
            tehty=2022,
            kategoria="Tekniikka",
            kieli="Suomi",
            avainsana="huolto, ylläpito",
            kuvaus="Testikirja Huolto-paneelin toimintojen varmistamiseen.",
            salattu_tiedosto="huolto_kirja.pdf.enc",
        )

    def tearDown(self) -> None:
        self.override.disable()
        self.tmpdir.cleanup()

    def test_huolto_kayttooikeudet_ja_base_html_perinta(self) -> None:
        huolto_url = reverse("huolto")

        # Anonyymi ohjataan kirjautumissivulle
        anon_resp = self.client.get(huolto_url)
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn(reverse("login"), anon_resp["Location"])

        # Tavallinen käyttäjä saa 403 Forbidden eikä näe Huolto-linkkiä navigaatiossa
        self.client.force_login(self.tavallinen)
        home_user = self.client.get(reverse("home"))
        self.assertNotContains(home_user, f'href="{huolto_url}"')
        self.assertEqual(self.client.get(huolto_url).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("huolto_vaihda_listaus", args=[self.kirja.kirja_id])).status_code,
            403,
        )

        # Ylläpitäjä näkee Huolto-linkin ja sivu perii base.html:n
        self.client.force_login(self.admin)
        home_admin = self.client.get(reverse("home"))
        self.assertContains(home_admin, f'href="{huolto_url}"')
        admin_resp = self.client.get(huolto_url)
        self.assertEqual(admin_resp.status_code, 200)
        self.assertTemplateUsed(admin_resp, "kirjasto/huolto.html")
        self.assertTemplateUsed(admin_resp, "base.html")

    def test_huolto_voi_muokata_lainaa_ja_lopettaa_sen_ennenaikaisesti(self) -> None:
        self.client.force_login(self.tavallinen)
        self.client.post(reverse("lainaa_kirja", args=[self.kirja.kirja_id]))
        laina = Laina.objects.get(kirja=self.kirja, kayttaja=self.tavallinen)
        temp_pdf = self.lib_root / "loans" / laina.valiaikainen_tiedosto
        self.assertTrue(temp_pdf.is_file())

        # Ylläpitäjä muokkaa lainan päättymisaikaa pidemmäksi
        self.client.force_login(self.admin)
        muokkaa_url = reverse("huolto_muokkaa_laina", args=[laina.laina_id])
        get_edit = self.client.get(muokkaa_url)
        self.assertEqual(get_edit.status_code, 200)
        self.assertTemplateUsed(get_edit, "base.html")

        uusi_alku = timezone.localtime(laina.laina_alku).replace(microsecond=0)
        uusi_loppu = uusi_alku + timedelta(days=7)
        post_edit = self.client.post(
            muokkaa_url,
            {
                "kirja": self.kirja.kirja_id,
                "kayttaja": self.tavallinen.pk,
                "laina_alku": uusi_alku.strftime("%Y-%m-%dT%H:%M:%S"),
                "laina_loppu": uusi_loppu.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            follow=True,
        )
        self.assertEqual(post_edit.status_code, 200)
        laina.refresh_from_db()
        self.assertEqual(laina.laina_loppu, uusi_loppu)
        self.assertTrue(temp_pdf.is_file())

        # Ylläpitäjä päättää lainan ennenaikaisesti Huollosta -> PDF-kopio poistuu heti
        lopeta_resp = self.client.post(
            reverse("huolto_lopeta_laina", args=[laina.laina_id]),
            follow=True,
        )
        self.assertEqual(lopeta_resp.status_code, 200)
        laina.refresh_from_db()
        self.assertLessEqual(laina.laina_loppu, timezone.now())
        self.assertFalse(temp_pdf.exists())

    def test_huolto_voi_poistaa_kirjan_valikoimasta_ja_palauttaa_sen(self) -> None:
        self.client.force_login(self.admin)
        listaus_url = reverse("huolto_vaihda_listaus", args=[self.kirja.kirja_id])

        # Poistetaan kirja valikoimasta (delist)
        self.client.post(listaus_url, {"toiminto": "piilota"}, follow=True)
        self.kirja.refresh_from_db()
        self.assertFalse(self.kirja.listattu)

        # Tavallinen käyttäjä ei näe kirjaa katalogissa eikä pääse lainaamaan sitä
        self.client.force_login(self.tavallinen)
        katalogi = self.client.get(reverse("kirja_lista"))
        self.assertNotContains(katalogi, "Huoltotestin käsikirja")
        self.assertEqual(
            self.client.get(reverse("kirja_tiedot", args=[self.kirja.kirja_id])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("lainaa_kirja", args=[self.kirja.kirja_id])).status_code,
            404,
        )

        # Ylläpitäjä palauttaa kirjan valikoimaan
        self.client.force_login(self.admin)
        self.client.post(listaus_url, {"toiminto": "palauta"}, follow=True)
        self.kirja.refresh_from_db()
        self.assertTrue(self.kirja.listattu)

        self.client.force_login(self.tavallinen)
        katalogi_palautettu = self.client.get(reverse("kirja_lista"))
        self.assertContains(katalogi_palautettu, "Huoltotestin käsikirja")

    def test_kommenttisyote_luontipaivasuodatin_ja_arvostelun_poisto(self) -> None:
        k_suomi = Kommentti.objects.create(
            kayttaja=self.tavallinen,
            kirja=self.kirja,
            arvosana=5,
            teksti="Vanhempi suomenkielinen kommentti: erittäin selkeä rakenne.",
        )
        k_kiina = Kommentti.objects.create(
            kayttaja=self.toinen,
            kirja=self.kirja,
            arvosana=4,
            teksti="管理员面板测试评论：按创建日期筛选功能非常实用！",
        )
        k_arabia_xss = Kommentti.objects.create(
            kayttaja=self.kolmas,
            kirja=self.kirja,
            arvosana=2,
            teksti='تعليق عربي للاختبار مع رموز خاصة <script>alert("huolto")</script>',
        )

        # Asetetaan kolmelle kommentille eri luontipäivät suodatuksen testaamiseksi
        Kommentti.objects.filter(pk=k_suomi.pk).update(
            luotu=timezone.make_aware(datetime(2026, 11, 10, 12, 0, 0))
        )
        Kommentti.objects.filter(pk=k_kiina.pk).update(
            luotu=timezone.make_aware(datetime(2026, 11, 20, 14, 30, 0))
        )
        Kommentti.objects.filter(pk=k_arabia_xss.pk).update(
            luotu=timezone.make_aware(datetime(2026, 11, 27, 9, 15, 0))
        )

        self.client.force_login(self.admin)
        huolto_url = reverse("huolto")

        # Ilman suodatinta kaikki 3 näkyvät ja XSS-merkinnät on pakattu turvallisesti
        kaikki = self.client.get(huolto_url)
        self.assertContains(kaikki, "Vanhempi suomenkielinen kommentti")
        self.assertContains(kaikki, "按创建日期筛选功能非常实用")
        self.assertContains(kaikki, "تعليق عربي للاختبار")
        self.assertNotContains(kaikki, '<script>alert("huolto")</script>')

        # Tarkka luontipäivän suodatin (?paiva=2026-11-20) näyttää vain kyseisen päivän kommentin
        tarkka = self.client.get(huolto_url, {"paiva": "2026-11-20"})
        self.assertContains(tarkka, "按创建日期筛选功能非常实用")
        self.assertNotContains(tarkka, "Vanhempi suomenkielinen kommentti")
        self.assertNotContains(tarkka, "تعليق عربي للاختبار")

        # Aikavälisuodatin (?alkaen=2026-11-19&asti=2026-11-27)
        aikavali = self.client.get(huolto_url, {"alkaen": "2026-11-19", "asti": "2026-11-27"})
        self.assertContains(aikavali, "按创建日期筛选功能非常实用")
        self.assertContains(aikavali, "تعليق عربي للاختبار")
        self.assertNotContains(aikavali, "Vanhempi suomenkielinen kommentti")

        # Virheellinen päiväsuodatin käsitellään kaatumatta
        virhe_pvm = self.client.get(huolto_url, {"paiva": "ei-kelvollinen-pvm"})
        self.assertEqual(virhe_pvm.status_code, 200)
        self.assertContains(virhe_pvm, "VVVV-KK-PP")

        # Arvostelun poistaminen Huollosta
        poisto = self.client.post(
            reverse("huolto_poista_kommentti", args=[k_arabia_xss.kommentti_id]),
            follow=True,
        )
        self.assertEqual(poisto.status_code, 200)
        self.assertFalse(Kommentti.objects.filter(pk=k_arabia_xss.kommentti_id).exists())


class PopulateDBAndServerScriptTests(TestCase):
    """PR5-testit: tietokannan idempotentti populointi sekä paikallinen tiedostopalvelinskripti."""

    def test_populate_db_luo_viisi_suomalaista_kayttajaa_lainat_ja_arvostelut_idempotentisti(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            pdf_src = tmp_root / "pdf"
            lib_dir = tmp_root / "library"
            pdf_src.mkdir()

            for item in SAMPLE_BOOKS:
                (pdf_src / item["source_filename"]).write_bytes(MINIMAL_PDF_BYTES)

            with override_settings(PDF_SOURCE_DIR=pdf_src, LIBRARY_ROOT=lib_dir):
                call_command("populate_db")

                self.assertEqual(Kirja.objects.count(), 10)
                self.assertEqual(User.objects.count(), 5)
                for u_info in FINNISH_USERS:
                    u = User.objects.get(username=u_info["username"])
                    self.assertTrue(u.password.startswith("pbkdf2_sha256$"))
                    self.assertTrue(u.check_password(u_info["password"]))

                lainat = list(Laina.objects.all())
                self.assertGreaterEqual(len(lainat), 1)
                self.assertLessEqual(len(lainat), 2)
                lainatut_kirja_idt = {l.kirja_id for l in lainat}
                self.assertEqual(len(lainatut_kirja_idt), len(lainat))

                for kirja in Kirja.objects.all():
                    self.assertGreaterEqual(
                        kirja.kommentit.count(),
                        3,
                        f"Kirjalla {kirja.nimi} tulisi olla vähintään 3 arvostelua.",
                    )

                kommentteja_ennen = Kommentti.objects.count()
                lainoja_ennen = Laina.objects.count()

                # Muokataan yhtä käyttäjää varmistaaksemme, ettei toinen ajo ylikirjoita olemassa olevaa dataa
                aino = User.objects.get(username="aino_korhonen")
                aino.first_name = "MuokattuAino"
                aino.save(update_fields=["first_name"])

                # Toinen populointiajo ei saa lisätä tai muuttaa mitään olemassa olevaa tietoa
                tulos2 = populoi_tietokanta()
                self.assertEqual(
                    tulos2,
                    {"kirjat": 0, "kayttajat": 0, "lainat": 0, "kommentit": 0},
                )
                self.assertEqual(Kirja.objects.count(), 10)
                self.assertEqual(User.objects.count(), 5)
                self.assertEqual(Laina.objects.count(), lainoja_ennen)
                self.assertEqual(Kommentti.objects.count(), kommentteja_ennen)
                aino.refresh_from_db()
                self.assertEqual(aino.first_name, "MuokattuAino")

    def test_serve_folder_skripti_portti_curl_ja_kaikkien_alikansioiden_selaus(self) -> None:
        script_path = Path(settings.BASE_DIR) / "serve_folder.sh"
        self.assertTrue(script_path.is_file())
        script_text = script_path.read_text(encoding="utf-8")
        self.assertIn("8002", script_text)
        self.assertIn("curl", script_text)

        # Virheellinen portti hylätään
        bad_proc = subprocess.run(
            [str(script_path), "99999", "--check"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(bad_proc.returncode, 0)

        # Etsitään vapaa portti ja testataan alikansioiden selaus curlilla
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            sub_dir = tmp_dir / "alikansio_yksi" / "alikansio_kaksi"
            sub_dir.mkdir(parents=True)
            # Lisätään jopa index.html varmistaaksemme, että alikansion listaus näkyy silti oletuksena
            (sub_dir / "index.html").write_text("<h1>Piilotettu</h1>", encoding="utf-8")
            monikielinen_tiedosto = "testi_ääkköset_中文_العربية.txt"
            (sub_dir / monikielinen_tiedosto).write_text("Sisältö OK", encoding="utf-8")

            proc = subprocess.Popen(
                [str(script_path), "--port", str(free_port), "--dir", str(tmp_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                base_url = f"http://127.0.0.1:{free_port}"
                valmis = False
                for _ in range(30):
                    chk = subprocess.run(
                        ["curl", "-fsS", f"{base_url}/"],
                        capture_output=True,
                        text=True,
                    )
                    if chk.returncode == 0:
                        valmis = True
                        break
                    time.sleep(0.1)
                self.assertTrue(valmis, "serve_folder.sh ei käynnistynyt ajoissa.")

                # Tarkistetaan juuri ja syvä alikansio curlilla
                root_listing = subprocess.run(
                    ["curl", "-fsS", f"{base_url}/"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                self.assertIn("alikansio_yksi/", root_listing)

                sub_listing = subprocess.run(
                    ["curl", "-fsS", f"{base_url}/alikansio_yksi/alikansio_kaksi/"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                self.assertIn("Directory listing for /alikansio_yksi/alikansio_kaksi/", sub_listing)
                self.assertIn("index.html", sub_listing)
            finally:
                proc.terminate()
                proc.wait(timeout=5)


class ComprehensiveFormsAndSecurityTests(TestCase):
    """PR6-lisätestit: kaikkien lomakkeiden yksikkötestit, reunatapaukset ja tietoturvatarkistukset."""

    def setUp(self) -> None:
        self.u1 = User.objects.create_user(username="lomake_kayttaja", password="LomakeSalasana123!")
        self.u2 = User.objects.create_user(username="toinen_kayttaja", password="ToinenSalasana123!")
        self.kirja1 = Kirja.objects.create(
            nimi="Lomaketestauksen käsikirja",
            tekija="Teppo Testaaja",
            tehty=2023,
            kategoria="Ohjelmistotekniikka",
            kieli="Suomi",
            avainsana="testaus, lomakkeet",
        )
        self.kirja2 = Kirja.objects.create(
            nimi="Toinen testikirja",
            tekija="Maija Malli",
            tehty=2024,
            kategoria="Ohjelmistotekniikka",
            kieli="Suomi",
        )

    def test_signup_form_reunatapaukset_ja_commit_false(self) -> None:
        # Käyttäjänimen kaltainen salasana hylätään UserAttributeSimilarityValidatorilla
        samankaltainen = SignupForm(
            data={"username": "virtanen_pekka", "password": "virtanen_pekka1"}
        )
        self.assertFalse(samankaltainen.is_valid())
        self.assertIn("password", samankaltainen.errors)

        # Pelkästään numeroita sisältävä salasana hylätään
        numerot = SignupForm(data={"username": "uusi_lukija", "password": "1234567890"})
        self.assertFalse(numerot.is_valid())
        self.assertIn("password", numerot.errors)

        # commit=False salaa salasanan tallentamatta vielä kantaan
        kelvollinen = SignupForm(
            data={"username": "  siistitty_nimi  ", "password": "VahvaUniikkiSalasana99!"}
        )
        self.assertTrue(kelvollinen.is_valid(), kelvollinen.errors)
        kayttaja = kelvollinen.save(commit=False)
        self.assertIsNone(kayttaja.pk)
        self.assertEqual(kayttaja.username, "siistitty_nimi")
        self.assertTrue(kayttaja.check_password("VahvaUniikkiSalasana99!"))

    def test_arvostelulomake_trimmaus_ja_rajapituudet(self) -> None:
        # Täsmälleen 2000 merkkiä (ympäröivien välilyöntien poiston jälkeen) hyväksytään
        tasan_max = "Hyvä! " + ("ä" * (Kommentti.MAX_TEKSTI_PITUUS - 6))
        lomake_ok = ArvosteluLomake(data={"arvosana": "5", "teksti": f"  {tasan_max}  \n"})
        self.assertTrue(lomake_ok.is_valid(), lomake_ok.errors)
        self.assertEqual(lomake_ok.cleaned_data["arvosana"], 5)
        self.assertEqual(lomake_ok.cleaned_data["teksti"], tasan_max)

        # Puuttuva tähtiarvosana hylätään suomenkielisellä virheilmoituksella
        ilman_tahtia = ArvosteluLomake(data={"teksti": "Hyvä kirja ilman tähtiä."})
        self.assertFalse(ilman_tahtia.is_valid())
        self.assertIn("arvosana", ilman_tahtia.errors)

    def test_lainamuokkauslomake_estaa_paallekkaiset_lainat_ja_virheellisen_aikajarjestyksen(self) -> None:
        nyt = timezone.localtime(timezone.now()).replace(microsecond=0)
        laina1 = Laina.objects.create(
            kirja=self.kirja1,
            kayttaja=self.u1,
            laina_alku=nyt,
            laina_loppu=nyt + timedelta(days=3),
        )
        laina2 = Laina.objects.create(
            kirja=self.kirja2,
            kayttaja=self.u2,
            laina_alku=nyt,
            laina_loppu=nyt + timedelta(days=2),
        )

        # Lopetusaika ennen alkamisaikaa hylätään
        vaarinpain = LainaMuokkausLomake(
            data={
                "kirja": self.kirja1.kirja_id,
                "kayttaja": self.u1.pk,
                "laina_alku": nyt.strftime("%Y-%m-%dT%H:%M:%S"),
                "laina_loppu": (nyt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            },
            instance=laina1,
        )
        self.assertFalse(vaarinpain.is_valid())
        self.assertIn("laina_loppu", vaarinpain.errors)

        # Lainaa 2 ei voi siirtää kirjalle 1 niin kauan kuin kirjalla 1 on aktiivinen laina 1
        konflikti = LainaMuokkausLomake(
            data={
                "kirja": self.kirja1.kirja_id,
                "kayttaja": self.u2.pk,
                "laina_alku": nyt.strftime("%Y-%m-%dT%H:%M:%S"),
                "laina_loppu": (nyt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            },
            instance=laina2,
        )
        self.assertFalse(konflikti.is_valid())
        self.assertIn("kirja", konflikti.errors)

        # Saman lainan (laina1) muokkaaminen omalle kirjalleen onnistuu
        oma_muokkaus = LainaMuokkausLomake(
            data={
                "kirja": self.kirja1.kirja_id,
                "kayttaja": self.u1.pk,
                "laina_alku": nyt.strftime("%Y-%m-%d %H:%M:%S"),
                "laina_loppu": (nyt + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
            },
            instance=laina1,
        )
        self.assertTrue(oma_muokkaus.is_valid(), oma_muokkaus.errors)

    def test_polun_ylityksen_esto_ja_avoimen_uudelleenohjauksen_esto(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaises(ValueError):
                _safe_join(base, "../../etc/passwd")
            with self.assertRaises(ValueError):
                _safe_join(base, ".")

        # Avoimen uudelleenohjauksen (open redirect) esto ulkoiseen osoitteeseen lopeta_laina-näkymässä
        nyt = timezone.now()
        laina = Laina.objects.create(
            kirja=self.kirja1,
            kayttaja=self.u1,
            laina_alku=nyt,
            laina_loppu=nyt + timedelta(days=1),
        )
        self.client.force_login(self.u1)
        resp = self.client.post(
            reverse("lopeta_laina", args=[laina.laina_id]),
            {"next": "https://paha-sivusto.example.com/kaappaus"},
        )
        self.assertRedirects(resp, reverse("kirja_tiedot", args=[self.kirja1.kirja_id]))

    def test_get_pyynnot_post_reiteille_eivat_muuta_tilaa_ja_kestokuvaukset_toimivat(self) -> None:
        self.client.force_login(self.u1)
        # GET lainaa_kirja tai tallenna_arvostelu ohjaa turvallisesti kirjasivulle luomatta tietueita
        r_laina = self.client.get(reverse("lainaa_kirja", args=[self.kirja1.kirja_id]))
        self.assertRedirects(r_laina, reverse("kirja_tiedot", args=[self.kirja1.kirja_id]))
        self.assertEqual(Laina.objects.count(), 0)

        r_arv = self.client.get(reverse("tallenna_arvostelu", args=[self.kirja1.kirja_id]))
        self.assertRedirects(r_arv, reverse("kirja_tiedot", args=[self.kirja1.kirja_id]))
        self.assertEqual(Kommentti.objects.count(), 0)

        # Tarkistetaan laina-ajan suomenkieliset kuvaukset eri aikayksiköille
        with override_settings(LOAN_DURATION_SECONDS=15):
            self.assertEqual(hae_laina_ajan_kuvaus(), "15 sekuntia")
        with override_settings(LOAN_DURATION_SECONDS=180):
            self.assertEqual(hae_laina_ajan_kuvaus(), "3 minuuttia")
        with override_settings(LOAN_DURATION_SECONDS=7200):
            self.assertEqual(hae_laina_ajan_kuvaus(), "2 tuntia")
        with override_settings(LOAN_DURATION_SECONDS=None, LOAN_MODE="default"):
            self.assertEqual(hae_laina_ajan_kuvaus(), "3 vuorokautta")

    def test_orpojen_lainatiedostojen_siivous_ja_virhetilanteet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_root = Path(tmpdir) / "library"
            loans_dir = lib_root / "loans"
            (loans_dir / "orpo_kansio").mkdir(parents=True)
            orpo_pdf = loans_dir / "orpo_kansio" / "ylimääräinen.pdf"
            orpo_pdf.write_bytes(MINIMAL_PDF_BYTES)

            with override_settings(LIBRARY_ROOT=lib_root):
                siivoa_vanhentuneet_lainat()
                self.assertFalse(orpo_pdf.exists())
                self.assertFalse((loans_dir / "orpo_kansio").exists())

                # Kirja ilman salattua tiedostoa nostaa selkeän LoanErrorin
                with self.assertRaises(LoanError):
                    luo_laina(self.u1, self.kirja1)



