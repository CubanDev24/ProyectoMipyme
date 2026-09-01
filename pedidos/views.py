from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from carta.models import Categoria
from inventario.models import Insumo
from inventario.models import TasaCambio
from inventario.models import RecetaItem
from usuarios.models import get_turno_abierto, mesas_del_turno
from .models import Mesa, Factura, Pedido


def _rol_permitido(*roles):
    def _check(user):
        return user.is_authenticated and user.role in roles
    return _check


@login_required
@user_passes_test(_rol_permitido('mesera'))
def mesera(request):
    categorias = Categoria.objects.prefetch_related('platos').all()
    categorias_con_platos = [c for c in categorias if c.platos.filter(disponible=True).exists()]
    turno = get_turno_abierto()
    mesas = mesas_del_turno(turno) if turno else Mesa.objects.none()
    return render(request, 'mesera/mesera.html', {
        'categorias': categorias_con_platos,
        'mesas': mesas,
        'turno': turno,
    })

@login_required
@user_passes_test(_rol_permitido('cocina'))
def cocina(request):
    return render(request, 'pedidos/cocina.html')


@login_required
@user_passes_test(_rol_permitido('cajera'))
def caja(request):
    return render(request, 'caja/caja.html', {
        'tasa_actual': TasaCambio.actual(),
    })

def factura_imprimir(request, pk):
    factura = get_object_or_404(Factura, pk=pk)
    return render(request, 'caja/factura_imprimir.html', {
        'factura': factura,
    })

def facturas_historial(request):
    facturas = Factura.objects.order_by('-creado_en')
    return JsonResponse({
        'facturas': [
            {
                'id': factura.id,
                'mesa_numero': factura.mesa_numero,
                'forma_pago_display': factura.get_forma_pago_display(),
                'total_cup': str(factura.total_cup),
                'creado_en': factura.creado_en.strftime('%d/%m/%Y %H:%M'),
            }
            for factura in facturas
        ],
    })

def caja_estadisticas(request):
    resumen = Factura.objects.aggregate(
        cantidad=Count('id'),
        total=Sum('total_cup'),
        efectivo=Sum('monto_efectivo_cup'),
        transferencia=Sum('monto_transferencia_cup'),
    )
    salidas = defaultdict(lambda: Decimal('0'))
    pedidos_descontados = Pedido.objects.filter(
        inventario_descontado=True,
    ).prefetch_related('items__plato__receta')
    for pedido in pedidos_descontados:
        for item in pedido.items.all():
            for receta in item.plato.receta.all():
                salidas[receta.insumo_id] += receta.cantidad * item.cantidad

    inventario = []
    for insumo in Insumo.objects.all():
        inventario.append({
            'nombre': insumo.nombre,
            'unidad': insumo.get_unidad_display(),
            'salida': str(salidas[insumo.id]),
            'stock_actual': str(insumo.stock_actual),
            'stock_bajo': insumo.stock_bajo,
        })
    return JsonResponse({
        'recaudacion': {
            'cantidad': resumen['cantidad'] or 0,
            'total': str(resumen['total'] or Decimal('0')),
            'efectivo': str(resumen['efectivo'] or Decimal('0')),
            'transferencia': str(resumen['transferencia'] or Decimal('0')),
        },
        'inventario': inventario,
    })
