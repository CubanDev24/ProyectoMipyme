from django.contrib import admin

from .models import Usuario, Turno, Notificacion


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('username', 'role', 'email', 'activo', 'ultimo_login_turno')
    list_filter = ('role', 'activo')
    search_fields = ('username', 'email', 'first_name', 'last_name')


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'estado', 'apertura', 'cierre')
    filter_horizontal = ('usuarios',)


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('destinatario', 'asunto', 'leida', 'creada_en')
    list_filter = ('leida',)
