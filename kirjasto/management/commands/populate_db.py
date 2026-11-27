from django.core.management.base import BaseCommand

from kirjasto.populate import populoi_tietokanta


class Command(BaseCommand):
    help = (
        "Populoi tietokanta idempotentisti: 5 suomalaista käyttäjää, "
        "2 kirjalainaa ja vähintään 3 arvostelua jokaiselle kirjalle."
    )

    def handle(self, *args, **options) -> None:
        tulokset = populoi_tietokanta()
        self.stdout.write(
            self.style.SUCCESS(
                "Tietokannan populointi valmis: "
                f"{tulokset['kirjat']} uutta kirjaa, "
                f"{tulokset['kayttajat']} uutta käyttäjää, "
                f"{tulokset['lainat']} uutta lainaa, "
                f"{tulokset['kommentit']} uutta arvostelua."
            )
        )
