import json
import uuid
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from django.test import TestCase
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from carta.models import Categoria, Plato
from asgiref.sync import async_to_sync
from inventario.models import Insumo, RecetaItem
from pedidos.consumers import CajaConsumer, ClienteConsumer, CocinaConsumer, MeseraConsumer, serializar_cuenta, serializar_factura, serializar_pedido
from pedidos.models import Factura, ItemPedido, Mesa, Pedido
from pedidos.views import en_turno
from usuarios.models import Turno, registrar_inicio_turno


class FacturaWorkflowTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Entradas', orden=1)
        self.plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Pizza',
            descripcion='Pizza de queso',
            precio='25.00',
            disponible=True,
            orden=1,
        )
        self.mesa = Mesa.objects.create(numero=7, abierta=True, activa=True)
        self.pedido = Pedido.objects.create(mesa=self.mesa, estado='listo', sesion_id=self.mesa.sesion_id)
        ItemPedido.objects.create(pedido=self.pedido, plato=self.plato, cantidad=2)

    def test_cliente_solicita_cuenta_con_resumen_completo(self):
        cuenta = serializar_cuenta([self.pedido])

        self.assertIsNotNone(cuenta)
        self.assertEqual(cuenta['mesa_numero'], self.mesa.numero)
        self.assertEqual(cuenta['total'], '57.00')
        self.assertEqual(cuenta['items'][0]['plato'], self.plato.nombre)

    def test_pedido_serializado_incluye_imagen_opcional_del_plato(self):
        self.plato.imagen = 'carta/pizza.png'
        self.plato.save(update_fields=['imagen'])

        pedido = serializar_pedido(self.pedido)

        self.assertTrue(pedido['items'][0]['imagen_url'].endswith('/media/carta/pizza.png'))

    def test_pedido_de_exteriores_conserva_mesa_y_etiqueta_de_zona(self):
        mesa_exterior = Mesa.objects.create(
            numero=80,
            zona='exteriores',
            numero_zona=1,
            abierta=True,
        )
        pedidos = async_to_sync(MeseraConsumer().crear_pedido)(
            mesa_exterior.id,
            [{'plato_id': self.plato.pk, 'cantidad': 1}],
            '',
        )
        pedido = Pedido.objects.get(pk=pedidos[0]['id'])
        cuenta = serializar_cuenta([pedido])

        self.assertEqual(pedido.mesa_id, mesa_exterior.id)
        self.assertEqual(pedidos[0]['mesa_id'], mesa_exterior.id)
        self.assertEqual(pedidos[0]['etiqueta'], 'Exteriores 1')
        self.assertEqual(cuenta['mesa_id'], mesa_exterior.id)
        self.assertEqual(cuenta['etiqueta'], 'Exteriores 1')

    def test_cocina_renderiza_miniaturas_para_los_platos(self):
        User = get_user_model()
        cocina = User.objects.create_user(username='cocina_imagenes', password='123456', role='cocina')
        self.client.force_login(cocina)

        response = self.client.get(reverse('pedidos:cocina'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'function urlImagenProducto')
        self.assertContains(response, 'class="cocina-item-imagen"')

    def test_caja_registra_factura_con_items_y_pdf(self):
        factura = Factura.objects.create(
            pedido=self.pedido,
            mesa_numero=self.mesa.numero,
            forma_pago='efectivo_cup',
            total_cup='50.00',
            monto_efectivo_cup='50.00',
            mesera_nombre='María',
            cajera_nombre='Lina',
            items_snapshot=[{
                'plato': self.plato.nombre,
                'cantidad': 2,
                'precio_unit': '25.00',
                'subtotal': '50.00',
            }],
        )

        resultado = serializar_factura(factura)

        self.assertIsNotNone(resultado)
        self.assertIn('items_snapshot', resultado)
        self.assertEqual(resultado['items_snapshot'][0]['plato'], self.plato.nombre)
        self.assertEqual(resultado['mesera_nombre'], 'María')
        self.assertEqual(resultado['cajera_nombre'], 'Lina')
        self.assertTrue(resultado['pdf_url'].startswith('/pedidos/caja/factura/'))

    def test_mesera_acepta_factura_y_envia_a_caja(self):
        cuenta = serializar_cuenta([self.pedido])

        payload = MeseraConsumer.build_factura_para_caja(cuenta)

        self.assertEqual(payload['tipo'], 'factura_solicitada')
        self.assertEqual(payload['mesa_numero'], self.mesa.numero)
        self.assertEqual(payload['factura']['mesa_numero'], self.mesa.numero)
        self.assertTrue(payload['aceptada_por_mesera'])

    def test_mesera_tiene_handler_para_factura_solicitada(self):
        self.assertTrue(hasattr(MeseraConsumer, 'factura_solicitada'))

    def test_solicitud_cliente_no_llega_a_cocina_hasta_que_mesera_la_envia(self):
        cliente = ClienteConsumer()
        cliente.mesa_numero = str(self.mesa.numero)
        solicitudes = async_to_sync(cliente.crear_pedido)([
            {'plato_id': self.plato.pk, 'cantidad': 1},
        ], '')
        solicitud = solicitudes[0]
        solicitud_id = solicitud['id']

        self.assertEqual(solicitud['estado'], 'por_confirmar')

        pedidos_cocina = async_to_sync(CocinaConsumer().get_pedidos_cocina)()
        self.assertNotIn(solicitud_id, [pedido['id'] for pedido in pedidos_cocina])

        pedido_enviado = async_to_sync(MeseraConsumer().enviar_pedido_a_estacion)(solicitud_id)

        self.assertEqual(pedido_enviado['estado'], 'pendiente')
        pedidos_cocina = async_to_sync(CocinaConsumer().get_pedidos_cocina)()
        self.assertIn(solicitud_id, [pedido['id'] for pedido in pedidos_cocina])

    def test_pedido_mixto_separa_bebidas_a_barra_y_no_a_cocina(self):
        categoria_bebidas = Categoria.objects.create(nombre='Bebidas', orden=2)
        bebida = Plato.objects.create(
            categoria=categoria_bebidas,
            nombre='Jugo',
            precio='10.00',
            disponible=True,
        )
        cliente = ClienteConsumer()
        cliente.mesa_numero = str(self.mesa.numero)

        pedidos = async_to_sync(cliente.crear_pedido)([
            {'plato_id': self.plato.pk, 'cantidad': 1},
            {'plato_id': bebida.pk, 'cantidad': 2},
        ], '')

        pedidos_por_destino = {pedido['destino']: pedido for pedido in pedidos}
        self.assertEqual(set(pedidos_por_destino), {'cocina', 'barra'})
        self.assertEqual(pedidos_por_destino['barra']['items'][0]['plato'], bebida.nombre)
        self.assertEqual(pedidos_por_destino['cocina']['items'][0]['plato'], self.plato.nombre)
        self.assertIsNone(async_to_sync(CocinaConsumer().cambiar_estado)(
            pedidos_por_destino['barra']['id'], 'en_preparacion',
        ))

        mesera = MeseraConsumer()
        pedido_barra = async_to_sync(mesera.enviar_pedido_a_estacion)(pedidos_por_destino['barra']['id'])
        pedido_cocina = async_to_sync(mesera.enviar_pedido_a_estacion)(pedidos_por_destino['cocina']['id'])
        self.assertEqual(pedido_barra['estado'], 'pendiente')

        pedidos_cocina = async_to_sync(CocinaConsumer().get_pedidos_cocina)()
        ids_cocina = [pedido['id'] for pedido in pedidos_cocina]
        self.assertIn(pedido_cocina['id'], ids_cocina)
        self.assertNotIn(pedido_barra['id'], ids_cocina)

    def test_pedido_para_llevar_separa_cocina_y_barra_sin_asignar_mesa(self):
        Turno.objects.create()
        categoria_bebidas = Categoria.objects.create(nombre='Bebidas para llevar', orden=2)
        bebida = Plato.objects.create(categoria=categoria_bebidas, nombre='Agua', precio='5.00', disponible=True)
        mesera = MeseraConsumer()

        pedidos = async_to_sync(mesera.crear_pedido_para_llevar)([
            {'plato_id': self.plato.pk, 'cantidad': 1},
            {'plato_id': bebida.pk, 'cantidad': 2},
        ], 'Sin azúcar', 'María Pérez', '55512345')

        self.assertEqual({pedido['destino'] for pedido in pedidos}, {'cocina', 'barra'})
        self.assertEqual({pedido['modalidad'] for pedido in pedidos}, {'para_llevar'})
        self.assertEqual({pedido['mesa_id'] for pedido in pedidos}, {None})
        self.assertEqual(len({pedido['grupo_para_llevar'] for pedido in pedidos}), 1)
        self.assertTrue(all(pedido['etiqueta'].startswith('Para llevar #') for pedido in pedidos))
        self.assertTrue(all(pedido['nombre_cliente'] == 'María Pérez' for pedido in pedidos))
        self.assertTrue(all(pedido['telefono_cliente'] == '55512345' for pedido in pedidos))
        self.assertEqual(
            set(Pedido.objects.filter(grupo_para_llevar=pedidos[0]['grupo_para_llevar'])
                .values_list('nombre_cliente', 'telefono_cliente')),
            {('María Pérez', '55512345')},
        )
        pedido_barra = next(pedido for pedido in pedidos if pedido['destino'] == 'barra')
        pedido_listo = async_to_sync(mesera.marcar_listo_para_llevar)(pedido_barra['id'])
        self.assertEqual(pedido_listo['estado'], 'listo')
        pedidos_activos = async_to_sync(mesera.get_pedidos_activos)()
        self.assertTrue(all(pedido['id'] in {item['id'] for item in pedidos_activos} for pedido in pedidos))

    def test_websocket_mesera_marca_listo_un_pedido_para_llevar_de_barra(self):
        Turno.objects.create()
        categoria_bebidas = Categoria.objects.create(nombre='Bebidas de barra', orden=2)
        bebida = Plato.objects.create(categoria=categoria_bebidas, nombre='Limonada', precio='7.00', disponible=True)
        mesera = MeseraConsumer()
        mesera.scope = {'user': None}
        pedido = async_to_sync(mesera.crear_pedido_para_llevar)([
            {'plato_id': bebida.pk, 'cantidad': 1},
        ], '', 'Carlos', '55500001')[0]
        mesera.channel_layer = AsyncMock()
        mesera.send = AsyncMock()

        async_to_sync(mesera.receive)(json.dumps({
            'accion': 'marcar_listo_para_llevar',
            'pedido_id': pedido['id'],
        }))

        pedido_db = Pedido.objects.get(pk=pedido['id'])
        self.assertEqual(pedido_db.estado, 'listo')
        self.assertTrue(any(
            call.args[0] == 'mesera' and call.args[1]['pedido']['estado'] == 'listo'
            for call in mesera.channel_layer.group_send.await_args_list
        ))

        async_to_sync(mesera.receive)(json.dumps({
            'accion': 'marcar_ticket_entregado',
            'grupo_para_llevar': pedido['grupo_para_llevar'],
        }))

        pedido_db.refresh_from_db()
        self.assertEqual(pedido_db.estado, 'entregado')

    def test_websocket_prepara_cobro_para_llevar_con_pago_elegido_por_mesera(self):
        grupo = uuid.uuid4()
        pedido = Pedido.objects.create(
            modalidad='para_llevar',
            grupo_para_llevar=grupo,
            estado='entregado',
        )
        ItemPedido.objects.create(pedido=pedido, plato=self.plato, cantidad=1)
        mesera = MeseraConsumer()
        mesera.scope = {'user': None}
        mesera.channel_layer = AsyncMock()
        mesera.send = AsyncMock()

        async_to_sync(mesera.receive)(json.dumps({
            'accion': 'preparar_cobro_para_llevar',
            'grupo_para_llevar': str(grupo),
            'forma_pago': 'usd',
            'tasa_cambio': '42.00',
        }))

        pedido.refresh_from_db()
        self.assertTrue(pedido.cuenta_solicitada)
        self.assertEqual(pedido.forma_pago_preseleccionada, 'usd')
        self.assertEqual(pedido.tasa_cambio_preseleccionada, 42)
        factura_evento = next(
            call.args[1]['factura']
            for call in mesera.channel_layer.group_send.await_args_list
            if call.args[0] == 'caja' and call.args[1]['type'] == 'factura_solicitada'
        )
        self.assertEqual(factura_evento['forma_pago'], 'usd')
        self.assertEqual(factura_evento['tasa_cambio'], '42.00')

    def test_no_se_crea_pedido_para_llevar_sin_turno_abierto(self):
        mesera = MeseraConsumer()

        pedidos = async_to_sync(mesera.crear_pedido_para_llevar)([
            {'plato_id': self.plato.pk, 'cantidad': 1},
        ], '')

        self.assertEqual(pedidos, [])
        self.assertFalse(Pedido.objects.filter(modalidad='para_llevar').exists())

    def test_websocket_rechaza_ticket_para_llevar_sin_datos_de_contacto(self):
        Turno.objects.create()
        mesera = MeseraConsumer()
        mesera.send = AsyncMock()

        async_to_sync(mesera.receive)(json.dumps({
            'accion': 'crear_pedido_para_llevar',
            'items': [{'plato_id': self.plato.pk, 'cantidad': 1}],
        }))

        self.assertFalse(Pedido.objects.filter(modalidad='para_llevar').exists())
        respuesta = json.loads(mesera.send.await_args.kwargs['text_data'])
        self.assertEqual(respuesta['tipo'], 'error_pedido')
        self.assertIn('nombre y el teléfono', respuesta['mensaje'])

    def test_entregar_y_cobrar_ticket_para_llevar_descuenta_stock_y_factura_sin_mesa(self):
        Turno.objects.create()
        insumo = Insumo.objects.create(nombre='Queso de ticket', categoria=self.categoria, stock_actual='10.00')
        categoria_bebidas = Categoria.objects.create(nombre='Bebidas para el ticket', orden=2)
        bebida = Plato.objects.create(categoria=categoria_bebidas, nombre='Limonada del ticket', precio='7.00', disponible=True)
        insumo_bebida = Insumo.objects.create(nombre='Limón de ticket', categoria=categoria_bebidas, stock_actual='8.00')
        RecetaItem.objects.create(plato=self.plato, insumo=insumo, cantidad='0.50')
        RecetaItem.objects.create(plato=bebida, insumo=insumo_bebida, cantidad='1.00')
        mesera = MeseraConsumer()
        mesera.scope = {'user': None}
        pedidos = async_to_sync(mesera.crear_pedido_para_llevar)([
            {'plato_id': self.plato.pk, 'cantidad': 2},
            {'plato_id': bebida.pk, 'cantidad': 1},
        ], '', 'Ana López', '55500002')
        grupo_para_llevar = pedidos[0]['grupo_para_llevar']
        pedido_id = next(pedido['id'] for pedido in pedidos if pedido['destino'] == 'cocina')
        pedido_bebida_id = next(pedido['id'] for pedido in pedidos if pedido['destino'] == 'barra')
        Pedido.objects.filter(pk=pedido_id).update(estado='listo')

        pedidos_entregados, alertas = async_to_sync(mesera.marcar_ticket_entregado)(grupo_para_llevar)

        self.assertEqual(pedidos_entregados, [])
        self.assertEqual(Pedido.objects.get(pk=pedido_id).estado, 'listo')
        self.assertEqual(Pedido.objects.get(pk=pedido_bebida_id).estado, 'pendiente')
        self.assertFalse(alertas)

        Pedido.objects.filter(pk=pedido_bebida_id).update(estado='listo')
        pedidos_entregados, alertas = async_to_sync(mesera.marcar_ticket_entregado)(grupo_para_llevar)

        self.assertEqual({pedido['estado'] for pedido in pedidos_entregados}, {'entregado'})
        self.assertEqual(Pedido.objects.filter(grupo_para_llevar=grupo_para_llevar, estado='entregado').count(), 2)
        self.assertFalse(alertas)
        insumo.refresh_from_db()
        self.assertEqual(str(insumo.stock_actual), '9.00')
        insumo_bebida.refresh_from_db()
        self.assertEqual(str(insumo_bebida.stock_actual), '7.00')
        pedidos_actualizados, cuenta = async_to_sync(mesera.preparar_cobro_para_llevar)(
            grupo_para_llevar,
            'usd',
            '40.00',
        )
        self.assertTrue(cuenta['es_para_llevar'])
        self.assertIsNone(cuenta['mesa_numero'])
        self.assertEqual(cuenta['total'], '57.00')
        self.assertEqual(cuenta['forma_pago'], 'usd')
        self.assertEqual(cuenta['tasa_cambio'], '40.00')
        self.assertEqual(cuenta['nombre_cliente'], 'Ana López')
        self.assertEqual(cuenta['telefono_cliente'], '55500002')
        self.assertTrue(pedidos_actualizados[0]['cuenta_solicitada'])
        self.assertTrue(all(pedido['forma_pago'] == 'usd' for pedido in pedidos_actualizados))
        cuentas_caja = async_to_sync(CajaConsumer().get_cuentas_caja)()
        cuenta_en_caja = next(item for item in cuentas_caja if item['grupo_para_llevar'] == grupo_para_llevar)
        self.assertEqual(cuenta_en_caja['etiqueta'], cuenta['etiqueta'])
        self.assertEqual(cuenta_en_caja['nombre_cliente'], 'Ana López')
        self.assertEqual(cuenta_en_caja['telefono_cliente'], '55500002')

        factura_data, error = async_to_sync(CajaConsumer().cerrar_pedido_con_pago)(
            pedido_id, 'usd', '40.00', mesera_nombre='Ana', cajera_nombre='Celia',
        )

        self.assertIsNone(error)
        self.assertTrue(factura_data['es_para_llevar'])
        self.assertIsNone(factura_data['mesa_numero'])
        self.assertEqual(factura_data['nombre_cliente'], 'Ana López')
        self.assertEqual(factura_data['telefono_cliente'], '55500002')
        factura = Factura.objects.get(pedido_id=pedido_id)
        self.assertIsNone(factura.mesa_numero)
        self.assertEqual(factura.total_cup, 57)
        self.assertEqual(factura.forma_pago, 'usd')
        self.assertEqual(factura.tasa_cambio, 40)
        response = self.client.get(reverse('pedidos:factura_imprimir_web', args=[factura.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Para llevar')
        self.assertContains(response, 'Ana López')
        self.assertContains(response, '55500002')
        historial = self.client.get(reverse('pedidos:facturas_historial')).json()['facturas']
        self.assertTrue(historial[0]['etiqueta'].startswith('Para llevar #'))

    def test_llamada_cliente_se_notifica_a_mesera_con_numero_de_mesa(self):
        cliente = ClienteConsumer()
        cliente.mesa_numero = str(self.mesa.numero)
        cliente.channel_layer = AsyncMock()
        cliente.send = AsyncMock()

        async_to_sync(cliente.receive)(json.dumps({'accion': 'llamar_camarero'}))

        cliente.channel_layer.group_send.assert_awaited_once_with('mesera', {
            'type': 'llamada_mesera',
            'mesa_numero': self.mesa.numero,
        })
        cliente.send.assert_awaited_once_with(text_data=json.dumps({
            'tipo': 'llamada_camarero_confirmada',
            'mesa_numero': self.mesa.numero,
        }))

    def test_en_turno_usa_la_relacion_directa_con_el_usuario(self):
        User = get_user_model()
        mesera = User.objects.create_user(username='mesera_relacion_turno', role='mesera')
        turno = Turno.objects.create()
        turno.usuarios.add(mesera)

        self.assertTrue(en_turno(mesera.username))

    def test_factura_usa_username_cuando_no_hay_nombre_personal(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User(username='mesera1')
        self.assertEqual(user.get_full_name(), '')
        self.assertEqual(user.get_full_name() or user.username, 'mesera1')

    def test_vista_mesera_usa_productos_reales_sin_datos_de_demostracion(self):
        User = get_user_model()
        mesera = User.objects.create_user(username='mesera_real', password='123456', role='mesera')
        self.client.force_login(mesera)
        self.plato.imagen = 'carta/pizza.png'
        self.plato.save(update_fields=['imagen'])

        response = self.client.get(reverse('pedidos:mesera'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plato.nombre)
        self.assertContains(response, 'data-activar-sonido')
        self.assertContains(response, 'id="call-alert"')
        self.assertContains(response, 'sonidoMesa();')
        self.assertContains(response, 'id="ready-alert"')
        self.assertContains(response, 'Pedido listo')
        self.assertContains(response, 'sonidoPedidoNuevo();')
        self.assertContains(response, 'Ver pedido')
        self.assertContains(response, 'id="nav-bar"')
        self.assertContains(response, 'id="view-bar"')
        self.assertContains(response, 'function renderBarView()')
        self.assertContains(response, 'id="nav-takeaway"')
        self.assertContains(response, 'id="takeaway-customer-name"')
        self.assertContains(response, 'id="takeaway-customer-phone"')
        self.assertContains(response, 'nombre_cliente: nombreCliente')
        self.assertContains(response, 'telefono_cliente: telefonoCliente')
        self.assertContains(response, 'id="view-takeaway"')
        self.assertContains(response, 'El administrador debe abrir el turno para recibir pedidos.')
        self.assertContains(response, 'function renderVistaParaLlevar()')
        self.assertContains(response, 'function marcarPedidoParaLlevarListo(pedidoId)')
        self.assertContains(response, 'function abrirPagoTicketEntregado(grupoParaLlevar)')
        self.assertContains(response, 'abrirPagoTicketEntregado(data.pedido.grupo_para_llevar)')
        self.assertContains(response, 'abrirPagoPendienteParaLlevar();')
        self.assertContains(response, 'id="comprobante-transferencia-preview-wrap"')
        self.assertContains(response, 'Imagen del pago en transferencia')
        self.assertContains(response, 'Ver imagen')
        self.assertNotContains(response, '<img id="comprobante-transferencia-preview"')
        self.assertContains(response, 'Seleccionar imagen')
        self.assertContains(response, '/media/carta/pizza.png')

    def test_vista_mesera_muestra_exteriores_configurados(self):
        User = get_user_model()
        mesera = User.objects.create_user(username='mesera_exteriores', password='123456', role='mesera')
        Turno.objects.create(cantidad_mesas=2, cantidad_mesas_exteriores=1)
        self.client.force_login(mesera)

        response = self.client.get(reverse('pedidos:mesera'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="nav-exteriors"')
        self.assertContains(response, 'name: "Exteriores 1"')
        self.assertNotContains(response, 'Hamburguesa Gourmet')
        self.assertNotContains(response, 'Mesa 08')

    def test_paginas_de_roles_reciben_si_usuario_esta_en_turno(self):
        User = get_user_model()
        mesera = User.objects.create_user(username='mesera_turno', password='123456', role='mesera')
        cocina = User.objects.create_user(username='cocina_turno', password='123456', role='cocina')
        cajera = User.objects.create_user(username='cajera_turno', password='123456', role='cajera')
        for usuario in (mesera, cocina, cajera):
            registrar_inicio_turno(usuario)

        paginas = (
            (mesera, 'pedidos:mesera'),
            (cocina, 'pedidos:cocina'),
            (cajera, 'pedidos:caja'),
            (cajera, 'pedidos:caja_estadisticas_pagina'),
        )
        for usuario, nombre_url in paginas:
            with self.subTest(usuario=usuario.username, pagina=nombre_url):
                self.client.force_login(usuario)
                response = self.client.get(reverse(nombre_url))

                self.assertEqual(response.status_code, 200)
                self.assertIs(response.context['en_turno'], True)

    def test_caja_muestra_titulo_y_enlace_para_el_comprobante_de_transferencia(self):
        User = get_user_model()
        cajera = User.objects.create_user(username='cajera_comprobante', password='123456', role='cajera')
        self.client.force_login(cajera)

        response = self.client.get(reverse('pedidos:caja'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cobro-comprobante"')
        self.assertContains(response, 'Imagen del pago en transferencia')
        self.assertContains(response, 'Ver imagen')
        self.assertNotContains(response, '<img src="${safeUrl}"')

    def test_mesera_puede_subir_y_revisar_comprobante_de_transferencia(self):
        User = get_user_model()
        mesera = User.objects.create_user(username='mesera_comprobante', password='123456', role='mesera')
        self.client.force_login(mesera)

        image_bytes = BytesIO()
        Image.new('RGB', (2, 2), color='white').save(image_bytes, format='PNG')
        image = SimpleUploadedFile('comprobante.png', image_bytes.getvalue(), content_type='image/png')

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse('pedidos:subir_comprobante_transferencia'), {
                'mesa_numero': self.mesa.numero,
                'comprobante': image,
            })

            self.assertEqual(response.status_code, 200)
            self.pedido.refresh_from_db()
            cuenta = serializar_cuenta([self.pedido])

            self.assertTrue(self.pedido.comprobante_transferencia)
            self.assertEqual(response.json()['comprobante_url'], cuenta['comprobante_url'])
            self.assertFalse(self.pedido.cuenta_solicitada)

    def test_mesera_puede_subir_comprobante_para_ticket_para_llevar_entregado(self):
        mesera = get_user_model().objects.create_user(username='mesera_ticket_comprobante', role='mesera')
        self.client.force_login(mesera)
        grupo = uuid.uuid4()
        pedido_comida = Pedido.objects.create(
            modalidad='para_llevar', grupo_para_llevar=grupo, estado='entregado',
        )
        pedido_bebida = Pedido.objects.create(
            modalidad='para_llevar', grupo_para_llevar=grupo, destino='barra', estado='listo',
        )
        image_bytes = BytesIO()
        Image.new('RGB', (2, 2), color='white').save(image_bytes, format='PNG')

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            upload_pendiente = SimpleUploadedFile('comprobante.png', image_bytes.getvalue(), content_type='image/png')
            response_pendiente = self.client.post(reverse('pedidos:subir_comprobante_transferencia'), {
                'grupo_para_llevar': str(grupo),
                'comprobante': upload_pendiente,
            })
            self.assertEqual(response_pendiente.status_code, 404)

            pedido_bebida.estado = 'entregado'
            pedido_bebida.save(update_fields=['estado'])
            image_bytes.seek(0)
            upload_entregado = SimpleUploadedFile('comprobante.png', image_bytes.getvalue(), content_type='image/png')
            response_entregado = self.client.post(reverse('pedidos:subir_comprobante_transferencia'), {
                'grupo_para_llevar': str(grupo),
                'comprobante': upload_entregado,
            })

            self.assertEqual(response_entregado.status_code, 200)
            pedido_comida.refresh_from_db()
            self.assertTrue(pedido_comida.comprobante_transferencia)
            self.assertEqual(response_entregado.json()['comprobante_url'], pedido_comida.comprobante_transferencia.url)

    def test_factura_imprimir_devuelve_pdf_descargable(self):
        factura = Factura.objects.create(
            pedido=self.pedido,
            mesa_numero=self.mesa.numero,
            forma_pago='efectivo_cup',
            total_cup='50.00',
            monto_efectivo_cup='50.00',
            items_snapshot=[{
                'plato': self.plato.nombre,
                'cantidad': 2,
                'precio_unit': '25.00',
                'subtotal': '50.00',
            }],
        )

        response = self.client.get(f'/pedidos/caja/factura/{factura.id}/imprimir/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename=', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_factura_imprimir_web_devuelve_ticket_para_impresora(self):
        factura = Factura.objects.create(
            pedido=self.pedido,
            mesa_numero=self.mesa.numero,
            forma_pago='efectivo_cup',
            total_cup='50.00',
            monto_efectivo_cup='50.00',
            items_snapshot=[{
                'plato': self.plato.nombre,
                'cantidad': 2,
                'precio_unit': '25.00',
                'subtotal': '50.00',
            }],
        )

        response = self.client.get(f'/pedidos/caja/factura/{factura.id}/imprimir-web/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'window.print()')
        self.assertContains(response, 'FACTURA DE CONSUMO')
        self.assertContains(response, 'Calle 100 entre 117 y 123')
        self.assertContains(response, 'Reparto Puente Nuevo, Marianao')
        self.assertContains(response, 'El ingrediente secreto siempre es tu visita. ¡Vuelve pronto!')

    def test_vista_estadisticas_caja_existe_y_usa_template(self):
        User = get_user_model()
        User.objects.create_user(username='cajera_test', password='123456', role='cajera')
        self.client.login(username='cajera_test', password='123456')
        response = self.client.get('/pedidos/caja/estadisticas/pagina/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Estadísticas acumuladas')
        self.assertContains(response, 'Panel de Caja')
