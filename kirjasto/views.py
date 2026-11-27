from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
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
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import SignupForm
from .models import Kirja, Laina
from .services import (
    LoanError,
    hae_laina_ajan_kuvaus,
    hae_lainan_pdf_polku,
    lopeta_laina,
    luo_laina,
    siivoa_vanhentuneet_lainat,
)


def home(request: HttpRequest) -> HttpResponse:
    """Render the home page showing authentication status, library summary, and active loans."""
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    kirjoja_yhteensa = Kirja.objects.count()
    lainassa_lkm = (
        Kirja.objects.filter(
            Exists(Laina.objects.filter(kirja=OuterRef("pk"), laina_loppu__gt=now))
        ).count()
    )
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
    """Search and browse PDF books in the library."""
    siivoa_vanhentuneet_lainat()
    now = timezone.now()

    hakusana = request.GET.get("q", "").strip()
    kategoria = request.GET.get("kategoria", "").strip()
    vain_saatavilla = request.GET.get("vain_saatavilla", "") in ("1", "true", "on")

    kirjat = Kirja.objects.prefetch_related(
        Prefetch(
            "lainat",
            queryset=Laina.objects.filter(laina_loppu__gt=now).select_related("kayttaja"),
            to_attr="aktiiviset_lainat",
        )
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
        Kirja.objects.exclude(kategoria="")
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


def kirja_tiedot(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Display details and loan status/actions for a single PDF book."""
    siivoa_vanhentuneet_lainat()
    kirja = get_object_or_404(Kirja, pk=kirja_id)
    aktiivinen_laina = kirja.aktiivinen_laina()
    on_oma_laina = bool(
        aktiivinen_laina
        and request.user.is_authenticated
        and aktiivinen_laina.kayttaja_id == request.user.pk
    )

    return render(
        request,
        "kirjasto/kirja_tiedot.html",
        {
            "kirja": kirja,
            "aktiivinen_laina": aktiivinen_laina,
            "on_oma_laina": on_oma_laina,
            "laina_ajan_kuvaus": hae_laina_ajan_kuvaus(),
        },
    )


@login_required
def lainaa_kirja(request: HttpRequest, kirja_id: int) -> HttpResponse:
    """Create a temporary unencrypted copy and loan record for the requested book."""
    if request.method != "POST":
        return redirect("kirja_tiedot", kirja_id=kirja_id)

    kirja = get_object_or_404(Kirja, pk=kirja_id)
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

    if laina.kayttaja_id != request.user.pk and not request.user.is_staff:
        return HttpResponseForbidden("Sinulla ei ole oikeutta lopettaa tätä lainaa.")

    lopeta_laina(laina)
    messages.success(
        request,
        f'Kirjan "{laina.kirja.nimi}" laina on päätetty ja väliaikainen PDF-kopio poistettu.',
    )

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("kirja_tiedot", kirja_id=laina.kirja_id)


@login_required
def laina_pdf(request: HttpRequest, tunniste: str) -> HttpResponse:
    """Serve the temporary unencrypted PDF file while the user's loan is active."""
    siivoa_vanhentuneet_lainat()
    laina = get_object_or_404(
        Laina.objects.select_related("kirja", "kayttaja"),
        laina_tunniste=tunniste,
    )

    if laina.kayttaja_id != request.user.pk and not request.user.is_staff:
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
