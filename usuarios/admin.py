from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.utils.html import format_html

from .models import Usuario, Turno, Notificacion


class PasswordToggleWidget(forms.PasswordInput):
    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        input_html = super().render(name, value, attrs, renderer)
        field_id = attrs.get('id', name)
        toggle_id = f'{field_id}_toggle'
        button = format_html(
            '<button type="button" id="{}" class="password-toggle-btn" onclick="var input=document.getElementById(\'{}\'); input.type=input.type===\'password\' ? \'text\' : \'password\'; this.textContent=input.type===\'password\' ? \'👁\' : \'🙈\'; this.title=input.type===\'password\' ? \'Mostrar contraseña\' : \'Ocultar contraseña\';">👁</button>',
            toggle_id,
            field_id,
        )
        return format_html('{}{}', input_html, button)


class UsuarioChangeForm(UserChangeForm):
    password = forms.CharField(
        label='Contraseña',
        required=False,
        widget=PasswordToggleWidget(render_value=True),
    )


class UsuarioCreationForm(UserCreationForm):
    password1 = forms.CharField(
        label='Contraseña',
        widget=PasswordToggleWidget(render_value=True),
    )
    password2 = forms.CharField(
        label='Confirmación de contraseña',
        widget=PasswordToggleWidget(render_value=True),
    )


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form = UsuarioChangeForm
    add_form = UsuarioCreationForm
    list_display = ('username', 'role', 'email', 'activo', 'ultimo_login_turno')
    list_filter = ('role', 'activo')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Información personal', {'fields': ('first_name', 'last_name', 'email', 'telefono')}),
        ('Permisos', {'fields': ('role', 'activo', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas importantes', {'fields': ('last_login', 'ultimo_login_turno', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'role', 'first_name', 'last_name', 'email', 'telefono', 'activo'),
        }),
    )

    def save_model(self, request, obj, form, change):
        password = form.cleaned_data.get('password1') or form.cleaned_data.get('password')
        if password:
            obj.set_password(password)
        super().save_model(request, obj, form, change)


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'estado', 'apertura', 'cierre')
    filter_horizontal = ('usuarios',)


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ('destinatario', 'asunto', 'leida', 'creada_en')
    list_filter = ('leida',)
