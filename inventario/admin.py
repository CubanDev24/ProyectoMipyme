from django.contrib import admin
from .models import Insumo, MovimientoInventario, RecetaItem, TasaCambio


class RecetaItemInline(admin.TabularInline):
    model = RecetaItem
    extra = 1


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'categoria', 'costo', 'precio', 'stock_actual', 'stock_minimo', 'stock_maximo', 'stock_bajo', 'activo']
    list_filter = ['activo', 'unidad']
    list_editable = ['stock_actual', 'stock_minimo', 'stock_maximo', 'activo']
    inlines = [RecetaItemInline]


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
