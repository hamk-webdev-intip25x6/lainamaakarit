from datetime import datetime, time as dt_time, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import ArvosteluLomake, LainaMuokkausLomake, SignupForm
from .models import Kirja, Kommentti, Laina
from .services import (
    LoanError,
    hae_laina_ajan_kuvaus,
    hae_lainan_pdf_polku,
    lopeta_laina,
    luo_laina,
    siivoa_vanhentuneet_lainat,
    synkronoi_muokattu_laina,
)


def _on_huolto_kayttaja(user) -> bool:
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def huolto_vaaditaan(view_func):
    """Decorator restricting access to admin/staff users for Huolto maintenance views."""

    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not _on_huolto_kayttaja(request.user):
            return HttpResponseForbidden("Vain ylläpitäjillä on pääsy Huolto-näkymään.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _turvallinen_ohjaus(request: HttpRequest, oletus_kohde: str, **kwargs) -> HttpResponse:
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(oletus_kohde, **kwargs)


def home(request: HttpRequest) -> HttpResponse:
    """Render the home page showing authentication status, library summary, and active loans."""
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    listatut_kirjat = Kirja.objects.filter(listattu=True)
    kirjoja_yhteensa = listatut_kirjat.count()
    lainassa_lkm = listatut_kirjat.filter(
        Exists(Laina.objects.filter(kirja=OuterRef("pk"), laina_loppu__gt=now))
    ).count()
    saatavilla_lkm = max(0, kirjoja_yhteensa - lainassa_lkm)

    aktiiviset_omat_lainat = []
    if request.user.is_authenticated:
        aktiiviset_omat_lainat = list(
            Laina.objects.filter(kayttaja=request.user, laina_loppu__gt=now)
            .select_related("kirja")
            .order_by("laina_loppu")
        )

    return render(
        request,
        "kirjasto/home.html",
        {
            "kirjoja_yhteensa": kirjoja_yhteensa,
            "saatavilla_lkm": saatavilla_lkm,
            "aktiiviset_omat_lainat": aktiiviset_omat_lainat,
        },
    )


def signup(request: HttpRequest) -> HttpResponse:
    """Create a new user account using the custom signup form."""
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Käyttäjätili luotiin onnistuneesti. Tervetuloa!")
            return redirect("home")
    else:
        form = SignupForm()

    return render(request, "kirjasto/signup.html", {"form": form})


def kirja_lista(request: HttpRequest) -> HttpResponse:
    """Search and browse listed PDF books in the library."""
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    hakusana = request.GET.get("q", "").strip()
    kategoria = request.GET.get("kategoria", "").strip()
    vain_saatavilla = request.GET.get("vain_saatavilla", "") in ("1", "true", "on")

    kirjat = Kirja.objects.filter(listattu=True).prefetch_related(
        Prefetch(
            "lainat",
            queryset=Laina.objects.filter(laina_loppu__gt=now).select_related("kayttaja"),
            to_attr="aktiiviset_lainat",
        ),
        Prefetch(
            "kommentit",
            queryset=Kommentti.objects.only("kirja_id", "arvosana"),
            to_attr="ladatut_kommentit",
        ),
    )

    if hakusana:
        q_filter = (
            Q(nimi__icontains=hakusana)
            | Q(tekija__icontains=hakusana)
            | Q(avainsana__icontains=hakusana)
            | Q(kategoria__icontains=hakusana)
            | Q(kieli__icontains=hakusana)
            | Q(kuvaus__icontains=hakusana)
        )
        if hakusana.lstrip("-").isdigit():
            q_filter |= Q(tehty=int(hakusana))
        kirjat = kirjat.filter(q_filter)

    if kategoria:
        kirjat = kirjat.filter(kategoria=kategoria)

    if vain_saatavilla:
        kirjat = kirjat.filter(
            ~Exists(Laina.objects.filter(kirja=OuterRef("pk"), laina_loppu__gt=now))
        )

    kategoriat = (
        Kirja.objects.filter(listattu=True)
        .exclude(kategoria="")
        .values_list("kategoria", flat=True)
        .distinct()
        .order_by("kategoria")
    )

    kirja_rivit = []
    for kirja in kirjat:
        aktiivinen = kirja.aktiiviset_lainat[0] if kirja.aktiiviset_lainat else None
        kirja_rivit.append(
            {
                "kirja": kirja,
                "aktiivinen_laina": aktiivinen,
                "on_oma_laina": bool(
                    aktiivinen
                    and request.user.is_authenticated
                    and aktiivinen.kayttaja_id == request.user.pk
                ),
                "mediaani": kirja.arvosana_mediaani(),
                "arvosteluja": kirja.arvostelujen_maara(),
            }
        )

    return render(
        request,
        "kirjasto/kirja_lista.html",
        {
            "kirja_rivit": kirja_rivit,
            "kategoriat": kategoriat,
            "hakusana": hakusana,
            "valittu_kategoria": kategoria,
            "vain_saatavilla": vain_saatavilla,
            "laina_ajan_kuvaus": hae_laina_ajan_kuvaus(),
        },
    )


def _rakenna_kirja_tiedot_konteksti(
    request: HttpRequest,
    kirja: Kirja,
    arvostelu_lomake: ArvosteluLomake | None = None,
) -> dict:
    aktiivinen_laina = kirja.aktiivinen_laina()
    on_oma_laina = bool(
        aktiivinen_laina
        and request.user.is_authenticated
        and aktiivinen_laina.kayttaja_id == request.user.pk
    )

    kommentit = list(kirja.kommentit.select_related("kayttaja").all())
    kirja.ladatut_kommentit = kommentit

    oma_arvostelu = None
    if request.user.is_authenticated:
        for k in kommentit:
            if k.kayttaja_id == request.user.pk:
                oma_arvostelu = k
                break

    if arvostelu_lomake is None:
        arvostelu_lomake = ArvosteluLomake(instance=oma_arvostelu)

    return {
        "kirja": kirja,
        "aktiivinen_laina": aktiivinen_laina,
        "on_oma_laina": on_oma_laina,
        "laina_ajan_kuvaus": hae_laina_ajan_kuvaus(),
        "kommentit": kommentit,
        "oma_arvostelu": oma_arvostelu,
        "arvostelu_lomake": arvostelu_lomake,
        "arvosana_mediaani": kirja.arvosana_mediaani(),
        "arvosana_keskiarvo": kirja.arvosana_keskiarvo(),
        "arvostelujen_maara": len(kommentit),
        "taydet_tahdet": kirja.taydet_tahdet(),
        "tahti_asteikko": range(1, 6),
        "max_arvostelu_pituus": Kommentti.MAX_TEKSTI_PITUUS,
        "on_huolto": _on_huolto_kayttaja(request.user),
    }


def kirja_tiedot(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Display details, rating summary, loan actions, and reviews for a single PDF book."""
    siivoa_vanhentuneet_lainat()
    kirja = get_object_or_404(Kirja, pk=kirja_id)
    if not kirja.listattu and not _on_huolto_kayttaja(request.user):
        raise Http404("Kirja on poistettu valikoimasta.")
    konteksti = _rakenna_kirja_tiedot_konteksti(request, kirja)
    return render(request, "kirjasto/kirja_tiedot.html", konteksti)


@login_required
def tallenna_arvostelu(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Create a new review or modify the current user's existing review for a book."""
    kirja = get_object_or_404(Kirja, pk=kirja_id)
    if not kirja.listattu and not _on_huolto_kayttaja(request.user):
        raise Http404("Kirja on poistettu valikoimasta.")
    if request.method != "POST":
        return redirect("kirja_tiedot", kirja_id=kirja.kirja_id)

    siivoa_vanhentuneet_lainat()
    oma_arvostelu = Kommentti.objects.filter(
        kirja=kirja, kayttaja=request.user
    ).first()
    on_muokkaus = oma_arvostelu is not None

    lomake = ArvosteluLomake(request.POST, instance=oma_arvostelu)
    if lomake.is_valid():
        arvostelu = lomake.save(commit=False)
        arvostelu.kirja = kirja
        arvostelu.kayttaja = request.user
        arvostelu.save()
        if on_muokkaus:
            messages.success(request, "Arvostelusi on päivitetty.")
        else:
            messages.success(request, "Kiitos! Arvostelusi on tallennettu.")
        return redirect("kirja_tiedot", kirja_id=kirja.kirja_id)

    konteksti = _rakenna_kirja_tiedot_konteksti(request, kirja, arvostelu_lomake=lomake)
    return render(request, "kirjasto/kirja_tiedot.html", konteksti, status=400)


@login_required
def lainaa_kirja(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Create a temporary unencrypted copy and loan record for the requested book."""
    if request.method != "POST":
        return redirect("kirja_tiedot", kirja_id=kirja_id)

    kirja = get_object_or_404(Kirja, pk=kirja_id)
    if not kirja.listattu:
        raise Http404("Kirja on poistettu valikoimasta.")

    try:
        laina = luo_laina(request.user, kirja)
        messages.success(
            request,
            f'Kirja "{kirja.nimi}" on nyt lainattu sinulle. '
            f"Väliaikainen PDF-linkki on käytettävissä {laina.laina_loppu:%d.%m.%Y klo %H.%M.%S} asti.",
        )
    except LoanError as err:
        messages.error(request, str(err))

    return redirect("kirja_tiedot", kirja_id=kirja.kirja_id)


@login_required
def lopeta_laina_view(request: HttpRequest, laina_id: int) -> HttpResponse:
    """End an active loan early and remove its temporary unencrypted PDF file."""
    laina = get_object_or_404(Laina.objects.select_related("kirja"), pk=laina_id)
    if request.method != "POST":
        return redirect("kirja_tiedot", kirja_id=laina.kirja_id)

    if laina.kayttaja_id != request.user.pk and not _on_huolto_kayttaja(request.user):
        return HttpResponseForbidden("Sinulla ei ole oikeutta lopettaa tätä lainaa.")

    lopeta_laina(laina)
    messages.success(
        request,
        f'Kirjan "{laina.kirja.nimi}" laina on päätetty ja väliaikainen PDF-kopio poistettu.',
    )

    return _turvallinen_ohjaus(request, "kirja_tiedot", kirja_id=laina.kirja_id)


@login_required
def laina_pdf(request: HttpRequest, tunniste: str) -> HttpResponse:
    """Serve the temporary unencrypted PDF file while the user's loan is active."""
    siivoa_vanhentuneet_lainat()
    laina = get_object_or_404(
        Laina.objects.select_related("kirja", "kayttaja"),
        laina_tunniste=tunniste,
    )

    if laina.kayttaja_id != request.user.pk and not _on_huolto_kayttaja(request.user):
        return HttpResponseForbidden("Tämä lainalinkki kuuluu toiselle käyttäjälle.")

    polku = hae_lainan_pdf_polku(laina)
    if polku is None:
        raise Http404("Lainausaika on päättynyt ja väliaikainen PDF-tiedosto on poistettu.")

    as_attachment = request.GET.get("download") == "1"
    response = FileResponse(
        polku.open("rb"),
        as_attachment=as_attachment,
        filename=polku.name,
        content_type="application/pdf",
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    return response


@login_required
def omat_lainat(request: HttpRequest) -> HttpResponse:
    """List the current user's active and past loans."""
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    kaikki_lainat = list(
        Laina.objects.filter(kayttaja=request.user)
        .select_related("kirja")
        .order_by("-laina_alku")
    )
    aktiiviset = [l for l in kaikki_lainat if l.laina_loppu > now]
    paattyneet = [l for l in kaikki_lainat if l.laina_loppu <= now]

    return render(
        request,
        "kirjasto/omat_lainat.html",
        {
            "aktiiviset_lainat": aktiiviset,
            "paattyneet_lainat": paattyneet,
        },
    )


def _paiva_alku(paiva_obj) -> datetime:
    return timezone.make_aware(datetime.combine(paiva_obj, dt_time.min))


def _paiva_loppu_eksklusiivinen(paiva_obj) -> datetime:
    return timezone.make_aware(datetime.combine(paiva_obj + timedelta(days=1), dt_time.min))


@huolto_vaaditaan
def huolto(request: HttpRequest) -> HttpResponse:
    """
    Huolto maintenance dashboard for admin users:
    - Modify book loans or end them early
    - Delist or re-list books
    - Feed of new comments with creation-date filter and review deletion
    """
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    paiva_str = request.GET.get("paiva", "").strip()
    alkaen_str = request.GET.get("alkaen", "").strip()
    asti_str = request.GET.get("asti", "").strip()

    kommentit_qs = Kommentti.objects.select_related("kayttaja", "kirja").order_by(
        "-luotu", "-kommentti_id"
    )

    paivavirhe = ""
    paiva_parsed = parse_date(paiva_str) if paiva_str else None
    alkaen_parsed = parse_date(alkaen_str) if alkaen_str else None
    asti_parsed = parse_date(asti_str) if asti_str else None

    if (paiva_str and not paiva_parsed) or (alkaen_str and not alkaen_parsed) or (asti_str and not asti_parsed):
        paivavirhe = "Annettu päivämääräsuodatin ei ole muodossa VVVV-KK-PP."

    if paiva_parsed:
        kommentit_qs = kommentit_qs.filter(
            luotu__gte=_paiva_alku(paiva_parsed),
            luotu__lt=_paiva_loppu_eksklusiivinen(paiva_parsed),
        )
    else:
        if alkaen_parsed:
            kommentit_qs = kommentit_qs.filter(luotu__gte=_paiva_alku(alkaen_parsed))
        if asti_parsed:
            kommentit_qs = kommentit_qs.filter(luotu__lt=_paiva_loppu_eksklusiivinen(asti_parsed))

    kommentit = list(kommentit_qs)

    kaikki_lainat = list(
        Laina.objects.select_related("kirja", "kayttaja").order_by("-laina_alku")
    )
    aktiiviset_lainat = [l for l in kaikki_lainat if l.laina_loppu > now]
    paattyneet_lainat = [l for l in kaikki_lainat if l.laina_loppu <= now][:20]

    kirjat = list(
        Kirja.objects.prefetch_related(
            Prefetch(
                "lainat",
                queryset=Laina.objects.filter(laina_loppu__gt=now).select_related("kayttaja"),
                to_attr="aktiiviset_lainat",
            )
        ).order_by("nimi")
    )

    return render(
        request,
        "kirjasto/huolto.html",
        {
            "kommentit": kommentit,
            "aktiiviset_lainat": aktiiviset_lainat,
            "paattyneet_lainat": paattyneet_lainat,
            "kirjat": kirjat,
            "paiva": paiva_str,
            "alkaen": alkaen_str,
            "asti": asti_str,
            "paivavirhe": paivavirhe,
            "tahti_asteikko": range(1, 6),
        },
    )


@huolto_vaaditaan
def huolto_muokkaa_laina(request: HttpRequest, laina_id: int) -> HttpResponse:
    """Edit an existing book loan's book, user, start time, or end time in Huolto."""
    siivoa_vanhentuneet_lainat()
    laina = get_object_or_404(Laina.objects.select_related("kirja", "kayttaja"), pk=laina_id)
    vanha_kirja_id = laina.kirja_id

    if request.method == "POST":
        lomake = LainaMuokkausLomake(request.POST, instance=laina)
        if lomake.is_valid():
            paivitetty = lomake.save()
            synkronoi_muokattu_laina(paivitetty, vanha_kirja_id=vanha_kirja_id)
            messages.success(
                request,
                f'Lainaa #{paivitetty.laina_id} ("{paivitetty.kirja.nimi}") päivitettiin onnistuneesti.',
            )
            return redirect("huolto")
    else:
        lomake = LainaMuokkausLomake(instance=laina)

    return render(
        request,
        "kirjasto/huolto_laina_muokkaa.html",
        {
            "laina": laina,
            "lomake": lomake,
        },
    )


@huolto_vaaditaan
def huolto_lopeta_laina(request: HttpRequest, laina_id: int) -> HttpResponse:
    """Admin action in Huolto to end a book loan early."""
    if request.method != "POST":
        return redirect("huolto")

    laina = get_object_or_404(Laina.objects.select_related("kirja", "kayttaja"), pk=laina_id)
    lopeta_laina(laina)
    messages.success(
        request,
        f'Käyttäjän {laina.kayttaja.username} laina kirjasta "{laina.kirja.nimi}" päätettiin ennenaikaisesti.',
    )
    return _turvallinen_ohjaus(request, "huolto")


@huolto_vaaditaan
def huolto_vaihda_listaus(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Admin action in Huolto to delist or re-list a book."""
    if request.method != "POST":
        return redirect("huolto")

    kirja = get_object_or_404(Kirja, pk=kirja_id)
    toiminto = request.POST.get("toiminto", "").strip()
    if toiminto == "piilota":
        kirja.listattu = False
    elif toiminto == "palauta":
        kirja.listattu = True
    else:
        kirja.listattu = not kirja.listattu
    kirja.save(update_fields=["listattu"])

    if kirja.listattu:
        messages.success(request, f'Kirja "{kirja.nimi}" palautettiin kirjaston valikoimaan.')
    else:
        messages.success(request, f'Kirja "{kirja.nimi}" poistettiin näkyvistä (piilotettu valikoimasta).')

    return _turvallinen_ohjaus(request, "huolto")


@huolto_vaaditaan
def huolto_poista_kommentti(request: HttpRequest, kommentti_id: int) -> HttpResponse:
    """Admin action in Huolto to delete a book review/comment."""
    if request.method != "POST":
        return redirect("huolto")

    kommentti = get_object_or_404(
        Kommentti.objects.select_related("kirja", "kayttaja"),
        pk=kommentti_id,
    )
    kirja_nimi = kommentti.kirja.nimi
    kayttaja_nimi = kommentti.kayttaja.username
    kommentti.delete()
    messages.success(
        request,
        f'Käyttäjän {kayttaja_nimi} arvostelu kirjasta "{kirja_nimi}" poistettiin.',
    )
    return _turvallinen_ohjaus(request, "huolto")
