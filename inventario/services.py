from .models import RecetaItem


def descontar_inventario_por_pedido(pedido):
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
            insumo.stock_actual = insumo.stock_actual - consumo
            insumo.save(update_fields=['stock_actual', 'actualizado_en'])

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
