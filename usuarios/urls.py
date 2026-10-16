from django.urls import path

from . import views

app_name = 'usuarios'
urlpatterns = [
    path('', views.landing, name='landing'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/inventario-ventas/', views.inventario_ventas_turno, name='inventario_ventas_turno'),
    path('dashboard/usuarios/', views.gestion_usuarios_view, name='gestion_usuarios'),
    path('turno/abrir/', views.abrir_turno_view, name='abrir_turno'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/<int:user_id>/estado/', views.cambiar_estado_usuario, name='cambiar_estado_usuario'),
    path('usuarios/<int:user_id>/editar/', views.editar_usuario, name='editar_usuario'),
    path('turno/mesas/', views.configurar_mesas_turno_view, name='configurar_mesas_turno'),
    path('turno/mesas/nueva/', views.agregar_mesa_turno_view, name='agregar_mesa_turno'),
    path('turno/mesas/<int:mesa_id>/estado/', views.cambiar_estado_mesa_turno_view, name='cambiar_estado_mesa_turno'),
    path('turno/cerrar/', views.cerrar_turno_view, name='cerrar_turno'),
    path('turnos/historial/', views.historial_turnos_view, name='historial_turnos'),
    path('turnos/<int:turno_id>/', views.historial_turno_detalle_view, name='historial_turno_detalle'),
    path('turnos/<int:turno_id>/ipv/', views.ipv_turno, name='ipv_turno'),
    path('turnos/<int:turno_id>/ipv/pdf/', views.ipv_turno_pdf, name='ipv_turno_pdf'),
    path('turnos/<int:turno_id>/ipv/excel/', views.ipv_turno_excel, name='ipv_turno_excel'),
]
