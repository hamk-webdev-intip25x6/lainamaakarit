from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("kirjat/", views.kirja_lista, name="kirja_lista"),
    path("kirjat/<int:kirja_id>/", views.kirja_tiedot, name="kirja_tiedot"),
    path("kirjat/<int:kirja_id>/lainaa/", views.lainaa_kirja, name="lainaa_kirja"),
    path(
        "kirjat/<int:kirja_id>/arvostele/",
        views.tallenna_arvostelu,
        name="tallenna_arvostelu",
    ),
    path("lainat/<int:laina_id>/lopeta/", views.lopeta_laina_view, name="lopeta_laina"),
    path("lainat/<str:tunniste>/pdf/", views.laina_pdf, name="laina_pdf"),
    path("omat-lainat/", views.omat_lainat, name="omat_lainat"),
    path("huolto/", views.huolto, name="huolto"),
    path(
        "huolto/lainat/<int:laina_id>/muokkaa/",
        views.huolto_muokkaa_laina,
        name="huolto_muokkaa_laina",
    ),
    path(
        "huolto/lainat/<int:laina_id>/lopeta/",
        views.huolto_lopeta_laina,
        name="huolto_lopeta_laina",
    ),
    path(
        "huolto/kirjat/<int:kirja_id>/listaus/",
        views.huolto_vaihda_listaus,
        name="huolto_vaihda_listaus",
    ),
    path(
        "huolto/kommentit/<int:kommentti_id>/poista/",
        views.huolto_poista_kommentti,
        name="huolto_poista_kommentti",
    ),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
]
