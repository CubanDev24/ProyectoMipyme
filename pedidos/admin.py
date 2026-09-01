from django.contrib import admin
from .models import Mesa, Pedido, ItemPedido, Factura

@admin.register(Mesa)
class MesaAdmin(admin.ModelAdmin):
    list_display = ['numero', 'activa']
    list_editable = ['activa']

class ItemInline(admin.TabularInline):
    model = ItemPedido
    extra = 0
    readonly_fields = ['plato', 'cantidad', 'nota']

@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ['id', 'mesa', 'estado', 'cuenta_solicitada', 'creado_en']
    list_filter = ['estado', 'cuenta_solicitada', 'mesa']
    inlines = [ItemInline]
    readonly_fields = ['creado_en', 'actualizado_en']


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ['id', 'mesa_numero', 'forma_pago', 'total_cup', 'creado_en']
    list_filter = ['forma_pago']
    readonly_fields = [f.name for f in Factura._meta.fields]
