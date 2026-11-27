from datetime import timedelta
from pathlib import Path
import tempfile
import time

from django.conf import settings
from django.contrib.auth import get_user_model, views as auth_views
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from .crypto import EncryptionError, decrypt_pdf_file, encrypt_pdf_file, is_encrypted_file
from .forms import SignupForm
from .models import Kirja, Kommentti, Laina
from .services import (
    SAMPLE_BOOKS,
    hae_laina_aika,
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
            }.issubset(kirjat_cols)
        )
        self.assertEqual(
            kommentit_cols,
            {"kommentti_id", "kayttaja_id", "kirja_id", "teksti", "arvosana"},
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

            # Väärä avain epäonnistuu
            with self.assertRaises(EncryptionError):
                decrypt_pdf_file(salattu, tmp / "vaara.pdf", secret="vaara-avain")
            self.assertFalse((tmp / "vaara.pdf").exists())

            # Yhden tavun peukalointi huomataan HMAC-tarkistuksessa
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

                # Toinen ajo on idempotentti eikä luo duplikaatteja
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

        # Lainaaja saa avata ja ladata väliaikaisen PDF:n linkistä
        pdf_url = reverse("laina_pdf", args=[laina.laina_tunniste])
        pdf_resp = self.client.get(pdf_url)
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(pdf_resp.streaming_content), MINIMAL_PDF_BYTES)

        dl_resp = self.client.get(pdf_url + "?download=1")
        self.assertEqual(dl_resp.status_code, 200)
        self.assertIn("attachment", dl_resp["Content-Disposition"])
        dl_resp.close()

        # Toinen käyttäjä ei pääse lainalinkkiin eikä voi lainata samaa kirjaa tai lopettaa lainaa
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

        # Lainaaja päättää lainan -> salaamaton tiedosto poistuu välittömästi ja kirja vapautuu
        self.client.force_login(self.lainaaja)
        lopetus_resp = self.client.post(
            reverse("lopeta_laina", args=[laina.laina_id]),
            follow=True,
        )
        self.assertEqual(lopetus_resp.status_code, 200)
        self.assertFalse(temp_pdf.exists())
        self.assertEqual(self.client.get(pdf_url).status_code, 404)

        # Nyt toinen käyttäjä pystyy lainaamaan kirjan
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

        # Heti lainauksen jälkeen tiedosto on olemassa ja luettavissa
        self.assertTrue(temp_pdf.is_file())
        first_get = self.client.get(pdf_url)
        self.assertEqual(first_get.status_code, 200)
        first_get.close()

        # Odotetaan sekunnin murto-osa yli laina-ajan
        time.sleep(0.55)

        # Tausta-ajastin on jo poistanut salaamattoman PDF-tiedoston levyltä
        self.assertFalse(temp_pdf.exists())
        # Linkki palauttaa 404 vanhenemisen jälkeen
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
