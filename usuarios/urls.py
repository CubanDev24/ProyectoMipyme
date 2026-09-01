from django.urls import path

from . import views

app_name = 'usuarios'
urlpatterns = [
    path('', views.landing, name='landing'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('turno/mesas/', views.configurar_mesas_turno_view, name='configurar_mesas_turno'),
    path('turno/carta/', views.configurar_carta_turno_view, name='configurar_carta_turno'),
    path('turno/cerrar/', views.cerrar_turno_view, name='cerrar_turno'),
]
