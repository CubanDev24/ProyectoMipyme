from django.db import transaction

from .models import MovimientoInventario, RecetaItem


def _registrar_movimiento(insumo, tipo, cantidad, stock_anterior, stock_posterior, usuario=None, motivo=''):
    MovimientoInventario.objects.create(
        insumo=insumo,
        producto_nombre=insumo.nombre,
        categoria_nombre=insumo.categoria.nombre if insumo.categoria else 'Sin categoría',
        tipo=tipo,
        cantidad=cantidad,
        stock_anterior=stock_anterior,
        stock_posterior=stock_posterior,
        unidad=insumo.unidad,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
        motivo=motivo,
    )


@transaction.atomic
def descontar_inventario_por_pedido(pedido, usuario=None):
    """
    Descuenta del inventario los insumos usados por cada plato del pedido,
    según la receta configurada por el administrador.

    Se ejecuta cuando la mesera marca el pedido como "servido" (lo recoge
    de cocina). Devuelve una lista de alertas (dicts) para mostrar en caja:
    - nivel 'bajo': el insumo quedó en o por debajo de su stock mínimo.
    - nivel 'critico': el insumo quedó en negativo (no había suficiente).

    Es idempotente: si el pedido ya fue descontado antes, no vuelve a tocar
    el inventario (evita doble descuento si la acción se dispara dos veces).
    """
    alertas = []
    if pedido.inventario_descontado:
        return alertas

    for item in pedido.items.select_related('plato').all():
        recetas = RecetaItem.objects.filter(plato=item.plato).select_related('insumo')
        for receta in recetas:
            insumo = receta.insumo
            consumo = receta.cantidad * item.cantidad
            stock_anterior = insumo.stock_actual
            insumo.stock_actual = stock_anterior - consumo
            insumo.save(update_fields=['stock_actual', 'actualizado_en'])
            _registrar_movimiento(
                insumo,
                'salida',
                consumo,
                stock_anterior,
                insumo.stock_actual,
                usuario=usuario,
                motivo=f'Pedido #{pedido.pk} servido',
            )

            if insumo.stock_actual < 0:
                alertas.append({
                    'insumo': insumo.nombre,
                    'nivel': 'critico',
                    'mensaje': f'{insumo.nombre}: no alcanzaba el stock (quedó en '
                               f'{insumo.stock_actual} {insumo.get_unidad_display()})',
                })
            elif insumo.stock_actual <= insumo.stock_minimo:
                alertas.append({
                    'insumo': insumo.nombre,
                    'nivel': 'bajo',
                    'mensaje': f'{insumo.nombre}: stock bajo ({insumo.stock_actual} '
                               f'{insumo.get_unidad_display()}, mínimo {insumo.stock_minimo})',
                })

    pedido.inventario_descontado = True
    pedido.save(update_fields=['inventario_descontado'])
    return alertas
