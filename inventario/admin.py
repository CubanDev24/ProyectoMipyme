from django.contrib import admin
from .models import Insumo, RecetaItem, TasaCambio


class RecetaItemInline(admin.TabularInline):
    model = RecetaItem
    extra = 1


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'unidad', 'stock_actual', 'stock_minimo', 'stock_bajo', 'activo']
    list_filter = ['activo', 'unidad']
    list_editable = ['stock_actual', 'stock_minimo', 'activo']
    inlines = [RecetaItemInline]


@admin.register(RecetaItem)
class RecetaItemAdmin(admin.ModelAdmin):
    list_display = ['plato', 'insumo', 'cantidad']
    list_filter = ['plato', 'insumo']


@admin.register(TasaCambio)
class TasaCambioAdmin(admin.ModelAdmin):
    list_display = ['valor', 'actualizada_en']
    readonly_fields = ['actualizada_en']
