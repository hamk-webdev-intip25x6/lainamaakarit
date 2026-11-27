from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("kirjat/", views.kirja_lista, name="kirja_lista"),
    path("kirjat/<int:kirja_id>/", views.kirja_tiedot, name="kirja_tiedot"),
    path("kirjat/<int:kirja_id>/lainaa/", views.lainaa_kirja, name="lainaa_kirja"),
    path("lainat/<int:laina_id>/lopeta/", views.lopeta_laina_view, name="lopeta_laina"),
    path("lainat/<str:tunniste>/pdf/", views.laina_pdf, name="laina_pdf"),
    path("omat-lainat/", views.omat_lainat, name="omat_lainat"),
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
