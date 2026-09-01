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
    path('turno/cerrar/', views.cerrar_turno_view, name='cerrar_turno'),
    path('turnos/historial/', views.historial_turnos_view, name='historial_turnos'),
    path('turnos/<int:turno_id>/', views.historial_turno_detalle_view, name='historial_turno_detalle'),
]
