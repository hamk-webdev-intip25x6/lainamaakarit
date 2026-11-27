from django.core.management.base import BaseCommand

from kirjasto.services import siivoa_vanhentuneet_lainat, synkronoi_esimerkkikirjat


class Command(BaseCommand):
    help = "Salaa esimerkkikirjat library/encrypted/-kansioon ja päivittää Kirja-tietueet."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reencrypt",
            action="store_true",
            help="Pakota kaikkien PDF-tiedostojen uudelleensalaus.",
        )

    def handle(self, *args, **options) -> None:
        poistetut = siivoa_vanhentuneet_lainat()
        salatut, luodut = synkronoi_esimerkkikirjat(
            pakota_uudelleensalaus=options["reencrypt"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Kirjasto synkronoitu: {salatut} PDF-tiedostoa salattu, "
                f"{luodut} uutta kirjaa lisätty, {poistetut} vanhentunutta lainaa siivottu."
            )
        )
