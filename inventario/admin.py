from django.contrib import admin
from django import forms
from decimal import Decimal
from .models import CategoriaInventario, Insumo, MovimientoInventario, RecetaItem, TasaCambio


class InsumoAdminForm(forms.ModelForm):
    class Meta:
        model = Insumo
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ('stock_actual', 'stock_minimo', 'stock_maximo', 'costo', 'precio'):
            campo = self.fields[nombre]
            modelo = Insumo._meta.get_field(nombre)
            maximo = Decimal(1).scaleb(modelo.max_digits - modelo.decimal_places) - Decimal(1).scaleb(-modelo.decimal_places)
            campo.max_value = maximo
            paso = Decimal(1).scaleb(-modelo.decimal_places)
            attrs = {
                'step': format(paso, 'f'),
                'max': format(maximo, 'f'),
            }
            if nombre != 'stock_actual':
                campo.min_value = Decimal('0')
                attrs['min'] = '0'
            campo.widget = forms.NumberInput(attrs=attrs)


class RecetaItemInline(admin.TabularInline):
    model = RecetaItem
    extra = 1


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    form = InsumoAdminForm
    list_display = ['nombre', 'tipo_inventario', 'categoria_inventario', 'costo', 'precio', 'stock_actual', 'stock_minimo', 'stock_maximo', 'stock_bajo', 'activo']
    list_filter = ['tipo_inventario', 'activo', 'unidad']
    list_editable = ['stock_actual', 'stock_minimo', 'stock_maximo', 'activo']
    inlines = [RecetaItemInline]


@admin.register(CategoriaInventario)
class CategoriaInventarioAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'orden']
    list_filter = ['tipo']
    search_fields = ['nombre']


@admin.register(RecetaItem)
class RecetaItemAdmin(admin.ModelAdmin):
    list_display = ['plato', 'insumo', 'cantidad']
    list_filter = ['plato', 'insumo']


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = ['creado_en', 'producto_nombre', 'categoria_nombre', 'tipo', 'cantidad', 'stock_anterior', 'stock_posterior', 'usuario']
    list_filter = ['tipo', 'categoria_nombre']
    search_fields = ['producto_nombre', 'usuario__username', 'usuario__first_name', 'usuario__last_name']
    readonly_fields = [field.name for field in MovimientoInventario._meta.fields]


@admin.register(TasaCambio)
class TasaCambioAdmin(admin.ModelAdmin):
    list_display = ['valor', 'actualizada_en']
    readonly_fields = ['actualizada_en']
