import json
from decimal import Decimal, ROUND_HALF_UP
import uuid

from django.db import transaction
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import F, Q
from django.utils import timezone
from .models import Pedido, ItemPedido, Mesa, Factura
from carta.models import Plato
from inventario.services import descontar_inventario_por_pedido
from usuarios.models import get_turno_abierto


def _redondear(valor):
    return valor.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _to_decimal(valor):
    if valor is None:
        return None
    try:
        return Decimal(str(valor).replace(',', '.').strip())
    except Exception:
        return None


def serializar_pedido(pedido):
    es_para_llevar = pedido.modalidad == 'para_llevar'
    codigo_para_llevar = pedido.grupo_para_llevar.hex[:8].upper() if pedido.grupo_para_llevar else None
    return {
        'id': pedido.pk,
        'mesa_id': pedido.mesa_id,
        'mesa_numero': pedido.mesa.numero if pedido.mesa_id else None,
        'modalidad': pedido.modalidad,
        'es_para_llevar': es_para_llevar,
        'grupo_para_llevar': str(pedido.grupo_para_llevar) if pedido.grupo_para_llevar else None,
        'codigo_para_llevar': codigo_para_llevar,
        'etiqueta': f'Para llevar #{codigo_para_llevar}' if es_para_llevar else pedido.mesa.etiqueta,
        'nombre_cliente': pedido.nombre_cliente,
        'telefono_cliente': pedido.telefono_cliente,
        'destino': pedido.destino,
        'estado': pedido.estado,
        'estado_display': pedido.get_estado_display(),
        'nota': pedido.nota,
        'cuenta_solicitada': pedido.cuenta_solicitada,
        'forma_pago': pedido.forma_pago_preseleccionada or None,
        'tasa_cambio': str(pedido.tasa_cambio_preseleccionada) if pedido.tasa_cambio_preseleccionada else None,
        'comprobante_url': pedido.comprobante_transferencia.url if pedido.comprobante_transferencia else None,
        'creado_en': pedido.creado_en.strftime('%H:%M'),
        'total': str(pedido.total()),
        'items': [
            {
                'id': i.pk,
                'plato_id': i.plato_id,
                'plato': i.plato.nombre,
                'imagen_url': i.plato.imagen.url if i.plato.imagen else None,
                'precio_unit': str(i.plato.precio),
                'cantidad': i.cantidad,
                'nota': i.nota,
                'subtotal': str(i.subtotal()),
            }
            for i in pedido.items.all()
        ],
    }


@transaction.atomic
def crear_pedidos_por_destino(
    mesa,
    items_data,
    nota,
    estado,
    modalidad='mesa',
    grupo_para_llevar=None,
    nombre_cliente='',
    telefono_cliente='',
):
    grupos = {'cocina': [], 'barra': []}
    for item_data in items_data:
        if not isinstance(item_data, dict):
            continue
        try:
            plato = Plato.objects.select_related('categoria').get(
                pk=item_data.get('plato_id'), disponible=True,
            )
        except (Plato.DoesNotExist, TypeError, ValueError):
            continue
        categoria = (plato.categoria.nombre or '').strip().casefold()
        destino = 'barra' if categoria.startswith('bebid') else 'cocina'
        grupos[destino].append((plato, item_data))

    pedidos = []
    for destino, items in grupos.items():
        if not items:
            continue
        pedido = Pedido.objects.create(
            mesa=mesa,
            sesion_id=mesa.sesion_id if mesa else None,
            modalidad=modalidad,
            grupo_para_llevar=grupo_para_llevar,
            nombre_cliente=nombre_cliente,
            telefono_cliente=telefono_cliente,
            nota=nota,
            estado=estado,
            destino=destino,
        )
        for plato, item_data in items:
            ItemPedido.objects.create(
                pedido=pedido,
                plato=plato,
                cantidad=item_data.get('cantidad', 1),
                nota=item_data.get('nota', ''),
            )
        pedidos.append(serializar_pedido(
            Pedido.objects.prefetch_related('items__plato').select_related('mesa').get(pk=pedido.pk)
        ))
    return pedidos


def serializar_cuenta(pedidos):
    pedidos = list(pedidos)
    if not pedidos:
        return None
    items = []
    total = Decimal('0')
    for pedido in pedidos:
        total += pedido.total()
        items.extend({
            'id': item.pk,
            'plato': item.plato.nombre,
            'precio_unit': str(item.plato.precio),
            'cantidad': item.cantidad,
            'nota': item.nota,
            'subtotal': str(item.subtotal()),
        } for item in pedido.items.all())
    es_para_llevar = pedidos[0].modalidad == 'para_llevar'
    codigo_para_llevar = pedidos[0].grupo_para_llevar.hex[:8].upper() if pedidos[0].grupo_para_llevar else None
    return {
        'id': pedidos[0].pk,
        'pedido_ids': [pedido.pk for pedido in pedidos],
        'mesa_id': pedidos[0].mesa_id,
        'mesa_numero': pedidos[0].mesa.numero if pedidos[0].mesa_id else None,
        'modalidad': pedidos[0].modalidad,
        'es_para_llevar': es_para_llevar,
        'grupo_para_llevar': str(pedidos[0].grupo_para_llevar) if pedidos[0].grupo_para_llevar else None,
        'codigo_para_llevar': codigo_para_llevar,
        'etiqueta': f'Para llevar #{codigo_para_llevar}' if es_para_llevar else pedidos[0].mesa.etiqueta,
        'nombre_cliente': pedidos[0].nombre_cliente,
        'telefono_cliente': pedidos[0].telefono_cliente,
        'cuenta_solicitada': True,
        'estado': 'servido',
        'estado_display': 'Factura solicitada',
        'nota': ' | '.join(pedido.nota for pedido in pedidos if pedido.nota),
        'creado_en': pedidos[0].creado_en.strftime('%H:%M'),
        'total': str(total),
        'forma_pago': pedidos[0].forma_pago_preseleccionada or None,
        'tasa_cambio': str(pedidos[0].tasa_cambio_preseleccionada) if pedidos[0].tasa_cambio_preseleccionada else None,
        'comprobante_url': pedidos[0].comprobante_transferencia.url if pedidos[0].comprobante_transferencia else None,
        'items': items,
        'pdf_url': None,
    }


def serializar_factura(factura):
    if not factura:
        return None
    formas_validas = dict(Factura.FORMA_PAGO_CHOICES)
    data = {
        'id': factura.id,
        'mesa_numero': factura.mesa_numero,
        'etiqueta': factura.pedido.mesa.etiqueta if factura.pedido.mesa_id else 'Para llevar',
        'nombre_cliente': factura.pedido.nombre_cliente,
        'telefono_cliente': factura.pedido.telefono_cliente,
        'pedido_ids': [factura.pedido_id],
        'forma_pago': factura.forma_pago,
        'forma_pago_display': formas_validas[factura.forma_pago],
        'total_cup': str(factura.total_cup),
        'monto_efectivo_cup': str(factura.monto_efectivo_cup),
        'monto_transferencia_cup': str(factura.monto_transferencia_cup),
        'monto_usd': str(factura.monto_usd),
        'tasa_cambio': str(factura.tasa_cambio) if factura.tasa_cambio else None,
        'mesera_nombre': getattr(factura, 'mesera_nombre', '') or '',
        'cajera_nombre': getattr(factura, 'cajera_nombre', '') or '',
        'creado_en': factura.creado_en.strftime('%d/%m/%Y %H:%M'),
        'items': [
            {
                'plato': item['plato'],
                'cantidad': item['cantidad'],
                'precio_unit': item['precio_unit'],
                'subtotal': item['subtotal'],
            }
            for item in factura.items_snapshot
        ],
        'items_snapshot': factura.items_snapshot,
        'pdf_url': f'/pedidos/caja/factura/{factura.id}/imprimir/',
    }
    return data


# ─── MESERA ──────────────────────────────────────────────────────────────────
class MeseraConsumer(AsyncWebsocketConsumer):
    """Canal de la mesera: ve todas las mesas y sus pedidos activos."""

    async def connect(self):
        await self.channel_layer.group_add('mesera', self.channel_name)
        await self.accept()
        pedidos = await self.get_pedidos_activos()
        mesas = await self.get_mesas()
        await self.send(text_data=json.dumps({
            'tipo': 'estado_inicial',
            'pedidos': pedidos,
            'mesas': mesas,
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard('mesera', self.channel_name)

    @staticmethod
    def build_factura_para_caja(cuenta):
        mesa_numero = cuenta.get('mesa_numero') if isinstance(cuenta, dict) else None
        return {
            'tipo': 'factura_solicitada',
            'mesa_numero': mesa_numero,
            'factura': cuenta,
            'mensaje': f"Mesa {mesa_numero} solicitó la factura",
            'aceptada_por_mesera': True,
        }

    async def receive(self, text_data):
        data = json.loads(text_data)
        accion = data.get('accion')

        if accion in ['crear_pedido', 'crear_pedido_para_llevar']:
            if accion == 'crear_pedido_para_llevar':
                nombre_cliente = str(data.get('nombre_cliente', '')).strip()
                telefono_cliente = str(data.get('telefono_cliente', '')).strip()
                if not nombre_cliente or not telefono_cliente:
                    await self.send(text_data=json.dumps({
                        'tipo': 'error_pedido',
                        'mensaje': 'Indica el nombre y el teléfono de la persona que solicita el ticket.',
                    }))
                    return
                if len(nombre_cliente) > 120 or len(telefono_cliente) > 30:
                    await self.send(text_data=json.dumps({
                        'tipo': 'error_pedido',
                        'mensaje': 'El nombre o el teléfono supera la longitud permitida.',
                    }))
                    return
                pedidos = await self.crear_pedido_para_llevar(
                    data.get('items', []),
                    data.get('nota', ''),
                    nombre_cliente,
                    telefono_cliente,
                )
            else:
                pedidos = await self.crear_pedido(
                    data['mesa_id'], data.get('items', []), data.get('nota', '')
                )
            if pedidos:
                for pedido in pedidos:
                    await self.send(text_data=json.dumps({'tipo': 'pedido_confirmado', 'pedido': pedido}))
                    if pedido['destino'] == 'cocina':
                        await self.channel_layer.group_send('cocina', {'type': 'pedido_nuevo', 'pedido': pedido})
                    await self.channel_layer.group_send('mesera', {'type': 'pedido_nuevo', 'pedido': pedido})
                    if pedido['mesa_numero'] is not None:
                        await self.channel_layer.group_send(
                            f"cliente_{pedido['mesa_numero']}",
                            {'type': 'pedido_nuevo', 'pedido': pedido},
                        )
            else:
                await self.send(text_data=json.dumps({
                    'tipo': 'error_pedido',
                    'mensaje': 'No se pudo crear el pedido. Verifica que la mesa esté abierta y los productos disponibles.',
                }))

        elif accion in ['enviar_pedido_a_estacion', 'enviar_pedido_a_cocina']:
            pedido = await self.enviar_pedido_a_estacion(data.get('pedido_id'))
            if pedido:
                if pedido['destino'] == 'cocina':
                    await self.channel_layer.group_send('cocina', {'type': 'pedido_nuevo', 'pedido': pedido})
                await self.channel_layer.group_send('mesera', {'type': 'pedido_actualizado', 'pedido': pedido})
                await self.channel_layer.group_send(
                    f"cliente_{pedido['mesa_numero']}",
                    {'type': 'pedido_actualizado', 'pedido': pedido},
                )
                await self.send(text_data=json.dumps({
                    'tipo': 'pedido_enviado_barra' if pedido['destino'] == 'barra' else 'pedido_enviado_cocina',
                    'pedido_id': pedido['id'],
                }))
            else:
                await self.send(text_data=json.dumps({
                    'tipo': 'error_pedido',
                    'mensaje': 'La solicitud ya no está pendiente o la mesa está cerrada.',
                }))

        elif accion == 'aceptar_factura':
            mesa_numero = data.get('mesa_numero')
            if not mesa_numero:
                return
            cuenta = await self.get_cuenta_mesa(mesa_numero)
            if not cuenta:
                return
            user = self.scope.get('user')
            if user and hasattr(user, 'get_full_name'):
                mesera_nombre = user.get_full_name() or user.username
            else:
                mesera_nombre = user.username if user else ''
            cuenta['mesera_nombre'] = mesera_nombre
            payload = self.build_factura_para_caja(cuenta)
            payload['mesera_nombre'] = mesera_nombre
            await self.channel_layer.group_send(
                'caja',
                {'type': 'factura_solicitada', 'factura': cuenta, 'mesa_numero': mesa_numero, 'aceptada_por_mesera': True, 'mesera_nombre': mesera_nombre}
            )
            await self.send(text_data=json.dumps({'tipo': 'factura_aceptada', **payload}))

        elif accion == 'preparar_cobro':
            mesa_numero = data.get('mesa_numero')
            forma_pago = data.get('forma_pago')
            if not mesa_numero or forma_pago not in dict(Factura.FORMA_PAGO_CHOICES):
                await self.send(text_data=json.dumps({'tipo': 'error_cobro', 'mensaje': 'Selecciona una forma de pago válida.'}))
                return
            await self.marcar_cuenta_solicitada_por_numero(mesa_numero)
            cuenta = await self.get_cuenta_mesa(mesa_numero)
            if not cuenta:
                await self.send(text_data=json.dumps({'tipo': 'error_cobro', 'mensaje': 'No hay una cuenta pendiente para esta mesa.'}))
                return
            cuenta['forma_pago'] = forma_pago
            cuenta['tasa_cambio'] = data.get('tasa_cambio')
            await self.guardar_pago_preseleccionado(mesa_numero, forma_pago, data.get('tasa_cambio'))
            user = self.scope.get('user')
            cuenta['mesera_nombre'] = (user.get_full_name() or user.username) if user else ''
            await self.channel_layer.group_send(
                'caja',
                {'type': 'factura_solicitada', 'factura': cuenta, 'mesa_numero': mesa_numero, 'mesera_nombre': cuenta['mesera_nombre']}
            )
            await self.send(text_data=json.dumps({'tipo': 'cobro_preparado', 'mesa_numero': mesa_numero}))

        elif accion == 'preparar_cobro_para_llevar':
            grupo = data.get('grupo_para_llevar')
            forma_pago = data.get('forma_pago')
            if forma_pago not in dict(Factura.FORMA_PAGO_CHOICES):
                await self.send(text_data=json.dumps({'tipo': 'error_cobro', 'mensaje': 'Selecciona una forma de pago válida.'}))
                return
            pedidos, cuenta = await self.preparar_cobro_para_llevar(
                grupo,
                forma_pago,
                data.get('tasa_cambio'),
            )
            if not cuenta:
                await self.send(text_data=json.dumps({
                    'tipo': 'error_cobro',
                    'mensaje': 'El ticket debe estar completamente entregado antes de enviarlo a caja.',
                }))
                return
            user = self.scope.get('user')
            cuenta['mesera_nombre'] = (user.get_full_name() or user.username) if user else ''
            await self.channel_layer.group_send('caja', {
                'type': 'factura_solicitada',
                'factura': cuenta,
                'mesa_numero': None,
                'mesera_nombre': cuenta['mesera_nombre'],
            })
            for pedido in pedidos:
                await self.channel_layer.group_send('mesera', {
                    'type': 'pedido_actualizado',
                    'pedido': pedido,
                })
            await self.send(text_data=json.dumps({
                'tipo': 'cobro_preparado_para_llevar',
                'grupo_para_llevar': grupo,
            }))

        elif accion == 'abrir_mesa':
            mesa, error = await self.abrir_mesa(data.get('mesa_id'))
            if error:
                await self.send(text_data=json.dumps({'tipo': 'error_mesa', 'mensaje': error}))
            else:
                await self.channel_layer.group_send('mesera', {'type': 'mesa_actualizada', 'mesa': mesa})
                await self.channel_layer.group_send(
                    f'cliente_{mesa["numero"]}', {'type': 'mesa_actualizada', 'mesa': mesa}
                )

        elif accion == 'cerrar_mesa':
            mesa, error = await self.cerrar_mesa(data.get('mesa_id'))
            if error:
                await self.send(text_data=json.dumps({'tipo': 'error_mesa', 'mensaje': error}))
            else:
                await self.channel_layer.group_send('mesera', {'type': 'mesa_actualizada', 'mesa': mesa})
                await self.channel_layer.group_send(
                    f'cliente_{mesa["numero"]}', {'type': 'mesa_actualizada', 'mesa': mesa}
                )

        elif accion == 'marcar_servido':
            # La mesera "recoge" el pedido de cocina y lo marca como servido:
            # este es el punto donde se descuenta el inventario usado por esos platos.
            pedido, alertas = await self.marcar_servido_y_descontar(data['pedido_id'])
            if pedido:
                await self._broadcast_actualizacion(pedido)
                if alertas:
                    await self.channel_layer.group_send('caja', {
                        'type': 'alerta_inventario',
                        'alertas': alertas,
                        'mesa_numero': pedido['mesa_numero'],
                    })

        elif accion == 'marcar_ticket_entregado':
            pedidos, alertas = await self.marcar_ticket_entregado(data.get('grupo_para_llevar'))
            for pedido in pedidos:
                await self._broadcast_actualizacion(pedido)
            if alertas and pedidos:
                await self.channel_layer.group_send('caja', {
                    'type': 'alerta_inventario',
                    'alertas': alertas,
                    'mesa_numero': None,
                    'etiqueta_pedido': pedidos[0]['etiqueta'],
                })

        elif accion == 'marcar_listo_para_llevar':
            pedido = await self.marcar_listo_para_llevar(data.get('pedido_id'))
            if pedido:
                await self._broadcast_actualizacion(pedido)

        elif accion in ['solicitar_cuenta', 'solicitar_factura']:
            pedidos = await self.marcar_cuenta_solicitada(data.get('mesa_id'))
            if pedidos:
                for pedido in pedidos:
                    await self._broadcast_actualizacion(pedido)
                cuenta = await self.get_cuenta_mesa(pedidos[0]['mesa_numero'])
                await self.channel_layer.group_send(
                    'caja', {'type': 'cuenta_actualizada', 'cuenta': cuenta}
                )
                await self.channel_layer.group_send(
                    f"cliente_{pedidos[0]['mesa_numero']}",
                    {'type': 'factura_solicitada', 'factura': cuenta},
                )

        elif accion == 'solicitar_cuenta_para_llevar':
            pedidos, cuenta = await self.solicitar_cuenta_para_llevar(data.get('grupo_para_llevar'))
            if cuenta:
                for pedido in pedidos:
                    await self.channel_layer.group_send('mesera', {
                        'type': 'pedido_actualizado',
                        'pedido': pedido,
                    })
                await self.channel_layer.group_send('caja', {
                    'type': 'cuenta_actualizada',
                    'cuenta': cuenta,
                })

    async def pedido_nuevo(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_nuevo', 'pedido': event['pedido']}))

    async def pedido_actualizado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_actualizado', 'pedido': event['pedido']}))

    async def llamada_mesera(self, event):
        await self.send(text_data=json.dumps({
            'tipo': 'llamada_mesera',
            'mesa_numero': event['mesa_numero'],
        }))

    async def factura_solicitada(self, event):
        factura = dict(event['factura'])
        if event.get('mesera_nombre'):
            factura['mesera_nombre'] = event['mesera_nombre']
        await self.send(text_data=json.dumps({
            'tipo': 'factura_solicitada',
            'factura': factura,
            'mesa_numero': event.get('mesa_numero', factura.get('mesa_numero')),
            'mensaje': f"Mesa {event.get('mesa_numero', factura.get('mesa_numero'))} solicitó la factura",
        }))

    async def pedido_cerrado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_cerrado', 'pedido_id': event['pedido_id']}))

    async def mesa_actualizada(self, event):
        await self.send(text_data=json.dumps({'tipo': 'mesa_actualizada', 'mesa': event['mesa']}))

    async def _broadcast_actualizacion(self, pedido):
        for grupo in ['cocina', 'mesera', 'caja']:
            await self.channel_layer.group_send(grupo, {'type': 'pedido_actualizado', 'pedido': pedido})
        if pedido['mesa_numero'] is not None:
            await self.channel_layer.group_send(
                f'cliente_{pedido["mesa_numero"]}',
                {'type': 'pedido_actualizado', 'pedido': pedido}
            )

    @database_sync_to_async
    def get_pedidos_activos(self):
        turno = get_turno_abierto()
        pedidos_mesa = Q(
            modalidad='mesa',
            mesa__abierta=True,
            sesion_id=F('mesa__sesion_id'),
        )
        pedidos_para_llevar = Q(pk__in=[])
        if turno:
            pedidos_para_llevar = Q(modalidad='para_llevar', creado_en__gte=turno.apertura)
        qs = Pedido.objects.filter(pedidos_mesa | pedidos_para_llevar).exclude(
            estado='cerrado',
        ).prefetch_related('items__plato').select_related('mesa')
        return [serializar_pedido(p) for p in qs]

    @database_sync_to_async
    def get_mesas(self):
        turno = get_turno_abierto()
        if turno is None:
            return []
        from usuarios.models import mesas_del_turno
        return [
            {
                'id': m.id,
                'numero': m.numero,
                'numero_zona': m.numero_zona or m.numero,
                'zona': m.zona,
                'etiqueta': m.etiqueta,
                'abierta': m.abierta,
            }
            for m in mesas_del_turno(turno)
        ]

    @database_sync_to_async
    def abrir_mesa(self, mesa_id):
        try:
            mesa = Mesa.objects.get(pk=mesa_id, activa=True)
        except Mesa.DoesNotExist:
            return None, 'La mesa no existe.'
        if mesa.abierta:
            return None, 'La mesa ya está abierta.'
        mesa.abierta = True
        mesa.sesion_id = uuid.uuid4()
        mesa.save(update_fields=['abierta', 'sesion_id'])
        return {
            'id': mesa.id,
            'numero': mesa.numero,
            'numero_zona': mesa.numero_zona or mesa.numero,
            'zona': mesa.zona,
            'etiqueta': mesa.etiqueta,
            'abierta': mesa.abierta,
        }, None

    @database_sync_to_async
    def cerrar_mesa(self, mesa_id):
        try:
            mesa = Mesa.objects.get(pk=mesa_id, activa=True)
        except Mesa.DoesNotExist:
            return None, 'La mesa no existe.'
        if not mesa.abierta:
            return None, 'La mesa ya está cerrada.'
        if Pedido.objects.filter(
            mesa=mesa, sesion_id=mesa.sesion_id,
        ).exclude(estado='cerrado').exists():
            return None, 'Cobra y cierra primero los pedidos activos de esta mesa.'
        mesa.abierta = False
        mesa.save(update_fields=['abierta'])
        return {
            'id': mesa.id,
            'numero': mesa.numero,
            'numero_zona': mesa.numero_zona or mesa.numero,
            'zona': mesa.zona,
            'etiqueta': mesa.etiqueta,
            'abierta': mesa.abierta,
        }, None

    @database_sync_to_async
    def crear_pedido(self, mesa_id, items_data, nota):
        try:
            mesa = Mesa.objects.get(pk=mesa_id, activa=True, abierta=True)
        except Mesa.DoesNotExist:
            return None
        if not items_data:
            return []
        return crear_pedidos_por_destino(mesa, items_data, nota, estado='pendiente')

    @database_sync_to_async
    def crear_pedido_para_llevar(self, items_data, nota, nombre_cliente='', telefono_cliente=''):
        nombre_cliente = nombre_cliente.strip()
        telefono_cliente = telefono_cliente.strip()
        if (
            not items_data
            or not nombre_cliente
            or not telefono_cliente
            or len(nombre_cliente) > 120
            or len(telefono_cliente) > 30
            or get_turno_abierto() is None
        ):
            return []
        return crear_pedidos_por_destino(
            None,
            items_data,
            nota,
            estado='pendiente',
            modalidad='para_llevar',
            grupo_para_llevar=uuid.uuid4(),
            nombre_cliente=nombre_cliente,
            telefono_cliente=telefono_cliente,
        )

    @database_sync_to_async
    def enviar_pedido_a_estacion(self, pedido_id):
        try:
            pedido = Pedido.objects.select_related('mesa').get(
                pk=pedido_id,
                estado='por_confirmar',
                mesa__abierta=True,
                sesion_id=F('mesa__sesion_id'),
            )
        except (Pedido.DoesNotExist, TypeError, ValueError):
            return None
        pedido.estado = 'pendiente'
        pedido.save(update_fields=['estado', 'actualizado_en'])
        return serializar_pedido(
            Pedido.objects.prefetch_related('items__plato').select_related('mesa').get(pk=pedido.pk)
        )

    @database_sync_to_async
    def cambiar_estado(self, pedido_id, estado):
        try:
            p = Pedido.objects.prefetch_related('items__plato').select_related('mesa').get(pk=pedido_id)
            p.estado = estado
            p.save()
            return serializar_pedido(p)
        except Pedido.DoesNotExist:
            return None

    @database_sync_to_async
    def marcar_servido_y_descontar(self, pedido_id):
        try:
            p = Pedido.objects.prefetch_related('items__plato').select_related('mesa').get(
                pk=pedido_id,
                modalidad='mesa',
            )
        except Pedido.DoesNotExist:
            return None, []
        p.estado = 'servido'
        p.save()
        usuario = self.scope.get('user')
        alertas = descontar_inventario_por_pedido(p, usuario=usuario)
        return serializar_pedido(p), alertas

    @database_sync_to_async
    def marcar_ticket_entregado(self, grupo_para_llevar):
        if not grupo_para_llevar:
            return [], []
        try:
            with transaction.atomic():
                pedidos = list(Pedido.objects.select_for_update().filter(
                    modalidad='para_llevar',
                    grupo_para_llevar=grupo_para_llevar,
                ).select_related('mesa').prefetch_related('items__plato').order_by('pk'))
                if not pedidos or any(pedido.estado != 'listo' for pedido in pedidos):
                    return [], []

                alertas = []
                for pedido in pedidos:
                    pedido.estado = 'entregado'
                    pedido.save(update_fields=['estado', 'actualizado_en'])
                    alertas.extend(descontar_inventario_por_pedido(
                        pedido,
                        usuario=self.scope.get('user'),
                    ))
                return [serializar_pedido(pedido) for pedido in pedidos], alertas
        except (TypeError, ValueError):
            return [], []

    @database_sync_to_async
    def marcar_listo_para_llevar(self, pedido_id):
        pedido = Pedido.objects.filter(
            pk=pedido_id,
            modalidad='para_llevar',
            destino='barra',
            estado__in=['pendiente', 'en_preparacion'],
        ).select_related('mesa').first()
        if not pedido:
            return None
        pedido.estado = 'listo'
        pedido.save(update_fields=['estado', 'actualizado_en'])
        return serializar_pedido(pedido)

    @database_sync_to_async
    def marcar_cuenta_solicitada(self, mesa_id):
        pedidos = list(Pedido.objects.filter(
            mesa_id=mesa_id,
            estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
        ).select_related('mesa').prefetch_related('items__plato'))
        if not pedidos:
            return []
        Pedido.objects.filter(pk__in=[p.pk for p in pedidos]).update(cuenta_solicitada=True)
        for pedido in pedidos:
            pedido.cuenta_solicitada = True
        return [serializar_pedido(pedido) for pedido in pedidos]

    @database_sync_to_async
    def solicitar_cuenta_para_llevar(self, grupo_para_llevar):
        if not grupo_para_llevar:
            return [], None
        pedidos = list(Pedido.objects.filter(
            modalidad='para_llevar',
            grupo_para_llevar=grupo_para_llevar,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))
        if not pedidos or any(pedido.estado != 'entregado' for pedido in pedidos):
            return [], None
        Pedido.objects.filter(pk__in=[pedido.pk for pedido in pedidos]).update(cuenta_solicitada=True)
        for pedido in pedidos:
            pedido.cuenta_solicitada = True
        return [serializar_pedido(pedido) for pedido in pedidos], serializar_cuenta(pedidos)

    @database_sync_to_async
    def preparar_cobro_para_llevar(self, grupo_para_llevar, forma_pago, tasa_cambio):
        if not grupo_para_llevar or forma_pago not in dict(Factura.FORMA_PAGO_CHOICES):
            return [], None
        pedidos = list(Pedido.objects.filter(
            modalidad='para_llevar',
            grupo_para_llevar=grupo_para_llevar,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))
        if not pedidos or any(pedido.estado != 'entregado' for pedido in pedidos):
            return [], None
        tasa = _to_decimal(tasa_cambio) if forma_pago == 'usd' else None
        Pedido.objects.filter(pk__in=[pedido.pk for pedido in pedidos]).update(
            cuenta_solicitada=True,
            forma_pago_preseleccionada=forma_pago,
            tasa_cambio_preseleccionada=tasa,
        )
        for pedido in pedidos:
            pedido.cuenta_solicitada = True
            pedido.forma_pago_preseleccionada = forma_pago
            pedido.tasa_cambio_preseleccionada = tasa
        return [serializar_pedido(pedido) for pedido in pedidos], serializar_cuenta(pedidos)

    @database_sync_to_async
    def get_cuenta_mesa(self, mesa_numero):
        return serializar_cuenta(Pedido.objects.filter(
            mesa__numero=mesa_numero,
            sesion_id=F('mesa__sesion_id'),
            cuenta_solicitada=True,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))

    @database_sync_to_async
    def guardar_pago_preseleccionado(self, mesa_numero, forma_pago, tasa_cambio):
        pedidos = Pedido.objects.filter(
            mesa__numero=mesa_numero,
            cuenta_solicitada=True,
        ).exclude(estado='cerrado')
        pedidos.update(
            forma_pago_preseleccionada=forma_pago,
            tasa_cambio_preseleccionada=_to_decimal(tasa_cambio),
        )

    @database_sync_to_async
    def marcar_cuenta_solicitada_por_numero(self, mesa_numero):
        Pedido.objects.filter(
            mesa__numero=mesa_numero,
            estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
        ).update(cuenta_solicitada=True)


# ─── COCINA ──────────────────────────────────────────────────────────────────
class CocinaConsumer(AsyncWebsocketConsumer):
    """Canal de cocina: recibe pedidos, cambia estado a en_preparacion / listo."""

    async def connect(self):
        await self.channel_layer.group_add('cocina', self.channel_name)
        await self.accept()
        pedidos = await self.get_pedidos_cocina()
        await self.send(text_data=json.dumps({'tipo': 'estado_inicial', 'pedidos': pedidos}))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard('cocina', self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        if data.get('accion') == 'cambiar_estado':
            nuevo = data['estado']
            if nuevo not in ['en_preparacion', 'listo']:
                return
            pedido = await self.cambiar_estado(data['pedido_id'], nuevo)
            if pedido:
                for grupo in ['cocina', 'mesera', 'caja']:
                    await self.channel_layer.group_send(grupo, {'type': 'pedido_actualizado', 'pedido': pedido})
                if pedido['mesa_numero'] is not None:
                    await self.channel_layer.group_send(
                        f'cliente_{pedido["mesa_numero"]}',
                        {'type': 'pedido_actualizado', 'pedido': pedido}
                    )

    async def pedido_nuevo(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_nuevo', 'pedido': event['pedido']}))

    async def pedido_actualizado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_actualizado', 'pedido': event['pedido']}))

    async def pedido_nuevo(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_nuevo', 'pedido': event['pedido']}))

    async def pedido_cerrado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_cerrado', 'pedido_id': event['pedido_id']}))

    @database_sync_to_async
    def get_pedidos_cocina(self):
        qs = Pedido.objects.filter(
            estado__in=['pendiente', 'en_preparacion', 'listo'],
            destino='cocina',
        )
        turno = get_turno_abierto()
        if turno:
            qs = qs.filter(Q(modalidad='mesa') | Q(modalidad='para_llevar', creado_en__gte=turno.apertura))
        else:
            qs = qs.filter(modalidad='mesa')
        qs = qs.prefetch_related('items__plato').select_related('mesa')
        return [serializar_pedido(p) for p in qs]

    @database_sync_to_async
    def cambiar_estado(self, pedido_id, estado):
        try:
            p = Pedido.objects.prefetch_related('items__plato').select_related('mesa').get(
                pk=pedido_id,
                destino='cocina',
            )
            p.estado = estado
            p.save()
            return serializar_pedido(p)
        except Pedido.DoesNotExist:
            return None


# ─── CAJA ────────────────────────────────────────────────────────────────────
class CajaConsumer(AsyncWebsocketConsumer):
    """Canal de caja: ve cuentas agrupadas por mesa, cobra y cierra."""

    async def connect(self):
        await self.channel_layer.group_add('caja', self.channel_name)
        await self.accept()
        cuentas = await self.get_cuentas_caja()
        await self.send(text_data=json.dumps({'tipo': 'estado_inicial', 'pedidos': cuentas}))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard('caja', self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        accion = data.get('accion')

        if accion in ['solicitar_cuenta', 'solicitar_factura']:
            cuenta = await self.marcar_cuenta_solicitada(data.get('mesa_id'), data.get('pedido_id'))
            if cuenta:
                await self.broadcast_cuenta(cuenta)

        elif accion == 'cerrar_pedido':
            pedido_id = data['pedido_id']
            forma_pago = data.get('forma_pago')
            tasa_cambio = data.get('tasa_cambio')
            mesera_nombre = data.get('mesera_nombre') or ''
            user = self.scope.get('user')
            if user and hasattr(user, 'get_full_name'):
                cajera_nombre = user.get_full_name() or user.username
            else:
                cajera_nombre = user.username if user else ''
            resultado, error = await self.cerrar_pedido_con_pago(pedido_id, forma_pago, tasa_cambio, mesera_nombre=mesera_nombre, cajera_nombre=cajera_nombre)
            if error:
                await self.send(text_data=json.dumps({'tipo': 'error_cobro', 'mensaje': error}))
            elif resultado:
                mesa_numero = resultado['mesa_numero']
                for grupo in ['caja', 'mesera', 'cocina']:
                    for cerrado_id in resultado['pedido_ids']:
                        await self.channel_layer.group_send(grupo, {'type': 'pedido_cerrado', 'pedido_id': cerrado_id})
                if not resultado['es_para_llevar']:
                    await self.channel_layer.group_send(
                        f'cliente_{mesa_numero}',
                        {'type': 'pedido_cerrado', 'pedido_id': pedido_id}
                    )
                    await self.channel_layer.group_send(
                        f'cliente_{mesa_numero}',
                        {'type': 'factura_final', 'factura': resultado}
                    )
                    await self.channel_layer.group_send(
                        'mesera',
                        {'type': 'mesa_actualizada', 'mesa': {
                            'id': resultado['mesa_id'],
                            'numero': mesa_numero,
                            'abierta': False,
                        }}
                    )
                    await self.channel_layer.group_send(
                        f'cliente_{mesa_numero}',
                        {'type': 'mesa_actualizada', 'mesa': {
                            'id': resultado['mesa_id'],
                            'numero': mesa_numero,
                            'abierta': False,
                        }}
                    )
                await self.send(text_data=json.dumps({'tipo': 'factura_registrada', 'factura': resultado}))

    async def pedido_nuevo(self, event):
        pedido = event['pedido']
        if pedido.get('cuenta_solicitada'):
            cuenta = await self.get_cuenta_para_llevar(pedido['grupo_para_llevar']) if pedido.get('es_para_llevar') else await self.get_cuenta_mesa(pedido['mesa_numero'])
            await self.send(text_data=json.dumps({'tipo': 'pedido_nuevo', 'pedido': cuenta}))

    async def pedido_actualizado(self, event):
        pedido = event['pedido']
        cuenta = await self.get_cuenta_para_llevar(pedido['grupo_para_llevar']) if pedido.get('es_para_llevar') else await self.get_cuenta_mesa(pedido['mesa_numero'])
        if cuenta:
            await self.send(text_data=json.dumps({'tipo': 'pedido_actualizado', 'pedido': cuenta}))

    async def pedido_cerrado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_cerrado', 'pedido_id': event['pedido_id']}))

    async def alerta_inventario(self, event):
        await self.send(text_data=json.dumps({
            'tipo': 'alerta_inventario',
            'alertas': event['alertas'],
            'mesa_numero': event['mesa_numero'],
            'etiqueta_pedido': event.get('etiqueta_pedido'),
        }))

    @database_sync_to_async
    def get_cuentas_caja(self):
        mesas = Mesa.objects.filter(
            pedidos__cuenta_solicitada=True,
            pedidos__estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
        ).distinct()
        cuentas = [serializar_cuenta(Pedido.objects.filter(
            mesa=mesa, cuenta_solicitada=True,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato')) for mesa in mesas]
        grupos = Pedido.objects.filter(
            modalidad='para_llevar',
            cuenta_solicitada=True,
        ).exclude(estado='cerrado').values_list('grupo_para_llevar', flat=True).distinct()
        for grupo in grupos:
            cuenta = serializar_cuenta(Pedido.objects.filter(
                modalidad='para_llevar',
                grupo_para_llevar=grupo,
                cuenta_solicitada=True,
            ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))
            if cuenta:
                cuentas.append(cuenta)
        return cuentas

    @database_sync_to_async
    def marcar_cuenta_solicitada(self, mesa_id=None, pedido_id=None):
        if pedido_id:
            pedido = Pedido.objects.filter(pk=pedido_id, modalidad='para_llevar').first()
            if pedido:
                grupo = Pedido.objects.filter(
                    modalidad='para_llevar',
                    grupo_para_llevar=pedido.grupo_para_llevar,
                ).exclude(estado='cerrado')
                if grupo.exclude(estado='entregado').exists():
                    return None
                grupo.update(cuenta_solicitada=True)
                return serializar_cuenta(grupo.select_related('mesa').prefetch_related('items__plato'))
        if not mesa_id and pedido_id:
            mesa_id = Pedido.objects.values_list('mesa_id', flat=True).filter(pk=pedido_id).first()
        if not mesa_id:
            return None
        pedidos = list(Pedido.objects.filter(
            mesa_id=mesa_id,
            estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
        ).select_related('mesa').prefetch_related('items__plato'))
        if not pedidos:
            return None
        Pedido.objects.filter(pk__in=[p.pk for p in pedidos]).update(cuenta_solicitada=True)
        return serializar_cuenta(Pedido.objects.filter(
            mesa_id=mesa_id, cuenta_solicitada=True,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))

    @database_sync_to_async
    def get_cuenta_mesa(self, mesa_numero):
        return serializar_cuenta(Pedido.objects.filter(
            mesa__numero=mesa_numero, cuenta_solicitada=True,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))

    @database_sync_to_async
    def get_cuenta_para_llevar(self, grupo_para_llevar):
        return serializar_cuenta(Pedido.objects.filter(
            modalidad='para_llevar',
            grupo_para_llevar=grupo_para_llevar,
            cuenta_solicitada=True,
        ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))

    async def cuenta_actualizada(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_actualizado', 'pedido': event['cuenta']}))

    async def factura_solicitada(self, event):
        factura = dict(event['factura'])
        if event.get('mesera_nombre'):
            factura['mesera_nombre'] = event['mesera_nombre']
        await self.send(text_data=json.dumps({
            'tipo': 'factura_solicitada',
            'factura': factura,
            'mesa_numero': event.get('mesa_numero', factura.get('mesa_numero')),
            'mensaje': f"Mesa {event.get('mesa_numero', factura.get('mesa_numero'))} solicitó la factura",
        }))

    async def broadcast_cuenta(self, cuenta):
        if cuenta['es_para_llevar']:
            for pedido_id in cuenta['pedido_ids']:
                await self.channel_layer.group_send('mesera', {'type': 'pedido_actualizado', 'pedido': {
                    'id': pedido_id,
                    'modalidad': 'para_llevar',
                    'es_para_llevar': True,
                    'grupo_para_llevar': cuenta['grupo_para_llevar'],
                    'cuenta_solicitada': True,
                }})
            await self.channel_layer.group_send('caja', {'type': 'cuenta_actualizada', 'cuenta': cuenta})
            await self.channel_layer.group_send('caja', {'type': 'factura_solicitada', 'factura': cuenta, 'mesa_numero': None})
            return
        for grupo in ['mesera', 'cocina']:
            for pedido_id in cuenta['pedido_ids']:
                await self.channel_layer.group_send(
                    grupo, {'type': 'pedido_actualizado', 'pedido': {
                        'id': pedido_id,
                        'mesa_numero': cuenta['mesa_numero'],
                        'cuenta_solicitada': True,
                    }}
                )
        await self.channel_layer.group_send('caja', {'type': 'cuenta_actualizada', 'cuenta': cuenta})
        await self.channel_layer.group_send('caja', {'type': 'factura_solicitada', 'factura': cuenta, 'mesa_numero': cuenta['mesa_numero']})
        await self.channel_layer.group_send(
            f'cliente_{cuenta["mesa_numero"]}',
            {'type': 'pedido_actualizado', 'pedido': cuenta}
        )
        await self.channel_layer.group_send(
            f'cliente_{cuenta["mesa_numero"]}',
            {'type': 'factura_solicitada', 'factura': cuenta}
        )

    @database_sync_to_async
    def cerrar_pedido_con_pago(self, pedido_id, forma_pago, tasa_cambio_raw, mesera_nombre='', cajera_nombre=''):
        """
        Verifica el pedido, calcula el monto según la forma de pago elegida
        por la cajera y registra la factura antes de cerrar la cuenta.
        Los montos se calculan aquí (no se confía en lo que mande el cliente)
        a partir del total real del pedido en la base de datos.
        """
        formas_validas = dict(Factura.FORMA_PAGO_CHOICES)
        if forma_pago not in formas_validas:
            return None, 'Selecciona una forma de pago válida.'

        try:
            principal = Pedido.objects.select_related('mesa').get(pk=pedido_id)
        except Pedido.DoesNotExist:
            return None, 'El pedido ya no existe.'

        if principal.modalidad == 'para_llevar':
            pedidos = list(Pedido.objects.filter(
                modalidad='para_llevar',
                grupo_para_llevar=principal.grupo_para_llevar,
                cuenta_solicitada=True,
            ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))
        else:
            pedidos = list(Pedido.objects.filter(
                mesa=principal.mesa,
                cuenta_solicitada=True,
            ).exclude(estado='cerrado').select_related('mesa').prefetch_related('items__plato'))
        if not pedidos:
            return None, 'La cuenta ya fue cerrada.'
        if any(hasattr(pedido, 'factura') for pedido in pedidos):
            return None, 'Esta cuenta ya fue cobrada.'

        total = sum((pedido.total() for pedido in pedidos), Decimal('0'))
        if total <= 0:
            return None, 'El pedido no tiene monto a cobrar.'

        monto_efectivo_cup = Decimal('0')
        monto_transferencia_cup = Decimal('0')
        monto_usd = Decimal('0')
        tasa_cambio = None

        if forma_pago == 'efectivo_cup':
            monto_efectivo_cup = total
        elif forma_pago == 'transferencia':
            monto_transferencia_cup = total
        elif forma_pago == 'mixto':
            mitad = _redondear(total / 2)
            monto_efectivo_cup = mitad
            monto_transferencia_cup = total - mitad
        elif forma_pago == 'usd':
            tasa_cambio = _to_decimal(tasa_cambio_raw)
            if not tasa_cambio or tasa_cambio <= 0:
                return None, 'Indica una tasa de cambio válida para cobrar en USD.'
            monto_usd = _redondear(total / tasa_cambio)

        items_snapshot = [
            {
                'plato': i.plato.nombre,
                'cantidad': i.cantidad,
                'precio_unit': str(i.plato.precio),
                'subtotal': str(i.subtotal()),
            }
            for pedido in pedidos
            for i in pedido.items.all()
        ]

        factura = Factura.objects.create(
            pedido=principal,
            mesa_numero=principal.mesa.numero if principal.mesa_id else None,
            forma_pago=forma_pago,
            total_cup=total,
            monto_efectivo_cup=monto_efectivo_cup,
            monto_transferencia_cup=monto_transferencia_cup,
            monto_usd=monto_usd,
            tasa_cambio=tasa_cambio,
            mesera_nombre=mesera_nombre,
            cajera_nombre=cajera_nombre,
            items_snapshot=items_snapshot,
        )

        Pedido.objects.filter(pk__in=[pedido.pk for pedido in pedidos]).update(
            estado='cerrado', cerrado_en=timezone.now()
        )
        if principal.mesa_id:
            principal.mesa.abierta = False
            principal.mesa.save(update_fields=['abierta'])

        factura_data = {
            'id': factura.id,
            'mesa_id': principal.mesa_id,
            'mesa_numero': factura.mesa_numero,
            'modalidad': principal.modalidad,
            'es_para_llevar': principal.modalidad == 'para_llevar',
            'etiqueta': serializar_pedido(principal)['etiqueta'],
            'nombre_cliente': principal.nombre_cliente,
            'telefono_cliente': principal.telefono_cliente,
            'pedido_ids': [pedido.pk for pedido in pedidos],
            'forma_pago': factura.forma_pago,
            'forma_pago_display': formas_validas[factura.forma_pago],
            'total_cup': str(factura.total_cup),
            'monto_efectivo_cup': str(factura.monto_efectivo_cup),
            'monto_transferencia_cup': str(factura.monto_transferencia_cup),
            'monto_usd': str(factura.monto_usd),
            'tasa_cambio': str(factura.tasa_cambio) if factura.tasa_cambio else None,
            'mesera_nombre': factura.mesera_nombre,
            'cajera_nombre': factura.cajera_nombre,
            'items_snapshot': items_snapshot,
            'items': [
                {
                    'plato': item['plato'],
                    'cantidad': item['cantidad'],
                    'precio_unit': item['precio_unit'],
                    'subtotal': item['subtotal'],
                }
                for item in items_snapshot
            ],
            'pdf_url': f'/pedidos/caja/factura/{factura.id}/imprimir/',
        }
        return factura_data, None


# ─── CLIENTE ─────────────────────────────────────────────────────────────────
class ClienteConsumer(AsyncWebsocketConsumer):
    """Canal del cliente en su mesa: envía pedido, ve estado, pide cuenta."""

    async def connect(self):
        self.mesa_numero = self.scope['url_route']['kwargs']['mesa_numero']
        self.group_name = f'cliente_{self.mesa_numero}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        pedidos, factura, mesa_abierta = await self.get_historial_cliente()
        await self.send(text_data=json.dumps({
            'tipo': 'estado_inicial_cliente',
            'pedidos': pedidos,
            'factura': factura,
            'mesa_abierta': mesa_abierta,
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        accion = data.get('accion')

        if accion == 'crear_pedido':
            pedidos = await self.crear_pedido(data.get('items', []), data.get('nota', ''))
            if pedidos:
                for pedido in pedidos:
                    await self.channel_layer.group_send(
                        self.group_name,
                        {'type': 'pedido_confirmado', 'pedido': pedido},
                    )
                    await self.channel_layer.group_send('mesera', {'type': 'pedido_nuevo', 'pedido': pedido})
            else:
                await self.send(text_data=json.dumps({
                    'tipo': 'error_pedido',
                    'mensaje': 'No se pudo crear el pedido. Verifica que la mesa esté abierta y los productos disponibles.',
                }))

        elif accion == 'llamar_camarero':
            mesa_numero = await self.mesa_abierta_para_llamada()
            if mesa_numero is None:
                await self.send(text_data=json.dumps({
                    'tipo': 'error_llamada_camarero',
                    'mensaje': 'La mesa está cerrada. No se pudo avisar a la mesera.',
                }))
                return
            await self.channel_layer.group_send('mesera', {
                'type': 'llamada_mesera',
                'mesa_numero': mesa_numero,
            })
            await self.send(text_data=json.dumps({
                'tipo': 'llamada_camarero_confirmada',
                'mesa_numero': mesa_numero,
            }))

        elif accion in ['solicitar_cuenta', 'solicitar_factura']:
            pedidos = await self.solicitar_cuenta(data['pedido_id'])
            if pedidos:
                factura = {
                    'id': pedidos[0].get('id') if isinstance(pedidos, list) else None,
                    'mesa_numero': self.mesa_numero,
                    'total_cup': str(sum(Decimal(str(pedido.get('total', '0'))) for pedido in pedidos)),
                    'items': [item for pedido in pedidos for item in pedido.get('items', [])],
                    'items_snapshot': [item for pedido in pedidos for item in pedido.get('items', [])],
                    'mesera_nombre': '',
                    'cajera_nombre': '',
                    'cuenta_solicitada': True,
                    'pdf_url': None,
                }
                await self.send(text_data=json.dumps({'tipo': 'factura_solicitada', 'factura': factura}))
                for grupo in ['caja', 'mesera']:
                    await self.channel_layer.group_send(
                        grupo,
                        {'type': 'factura_solicitada', 'factura': factura, 'mesa_numero': self.mesa_numero}
                    )
                    for pedido in pedidos:
                        await self.channel_layer.group_send(grupo, {'type': 'pedido_actualizado', 'pedido': pedido})

        elif accion == 'cargar_historial':
            pedidos, factura, mesa_abierta = await self.get_historial_cliente()
            await self.send(text_data=json.dumps({
                'tipo': 'historial_cliente',
                'pedidos': pedidos,
                'factura': factura,
                'mesa_abierta': mesa_abierta,
            }))

    async def pedido_actualizado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_actualizado', 'pedido': event['pedido']}))

    async def pedido_confirmado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_confirmado', 'pedido': event['pedido']}))

    async def pedido_nuevo(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_nuevo', 'pedido': event['pedido']}))

    async def pedido_cerrado(self, event):
        await self.send(text_data=json.dumps({'tipo': 'pedido_cerrado', 'pedido_id': event['pedido_id']}))

    async def factura_final(self, event):
        await self.send(text_data=json.dumps({'tipo': 'factura_final', 'factura': event['factura']}))

    async def factura_solicitada(self, event):
        await self.send(text_data=json.dumps({
            'tipo': 'factura_solicitada',
            'factura': event['factura'],
            'mesa_numero': event.get('mesa_numero', self.mesa_numero),
            'mensaje': f"Mesa {event.get('mesa_numero', self.mesa_numero)} solicitó la factura",
        }))

    async def mesa_actualizada(self, event):
        await self.send(text_data=json.dumps({'tipo': 'mesa_actualizada', 'mesa': event['mesa']}))

    @database_sync_to_async
    def mesa_abierta_para_llamada(self):
        mesa = Mesa.objects.filter(
            numero=self.mesa_numero,
            activa=True,
            abierta=True,
        ).values_list('numero', flat=True).first()
        return mesa

    @database_sync_to_async
    def get_historial_cliente(self):
        mesa = Mesa.objects.filter(numero=self.mesa_numero, activa=True).first()
        if not mesa or not mesa.abierta:
            return [], None, False
        pedidos = Pedido.objects.filter(
            mesa=mesa,
            sesion_id=mesa.sesion_id,
        ).select_related('mesa').prefetch_related('items__plato')
        factura = Factura.objects.filter(
            pedido__mesa=mesa,
            pedido__sesion_id=mesa.sesion_id,
        ).order_by('-creado_en').first()
        factura_data = None
        if factura:
            factura_data = serializar_factura(factura)
        return [serializar_pedido(pedido) for pedido in pedidos], factura_data, True

    @database_sync_to_async
    def crear_pedido(self, items_data, nota):
        try:
            mesa = Mesa.objects.get(numero=self.mesa_numero, activa=True, abierta=True)
        except Mesa.DoesNotExist:
            return None
        if not items_data:
            return []
        return crear_pedidos_por_destino(mesa, items_data, nota, estado='por_confirmar')

    @database_sync_to_async
    def solicitar_cuenta(self, pedido_id):
        try:
            p = Pedido.objects.select_related('mesa').get(
                pk=pedido_id, mesa__numero=self.mesa_numero,
                mesa__abierta=True,
                sesion_id=F('mesa__sesion_id'),
            )
            pedidos = list(Pedido.objects.filter(
                mesa=p.mesa,
                sesion_id=p.sesion_id,
                estado__in=['pendiente', 'en_preparacion', 'listo', 'servido'],
            ).select_related('mesa').prefetch_related('items__plato'))
            Pedido.objects.filter(pk__in=[pedido.pk for pedido in pedidos]).update(cuenta_solicitada=True)
            for pedido in pedidos:
                pedido.cuenta_solicitada = True
            return [serializar_pedido(pedido) for pedido in pedidos]
        except Pedido.DoesNotExist:
            return []
