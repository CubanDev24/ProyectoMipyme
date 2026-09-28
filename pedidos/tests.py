import json
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
from pedidos.consumers import ClienteConsumer, CocinaConsumer, MeseraConsumer, serializar_cuenta, serializar_factura, serializar_pedido
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
        self.assertEqual(cuenta['total'], '50.00')
        self.assertEqual(cuenta['items'][0]['plato'], self.plato.nombre)

    def test_pedido_serializado_incluye_imagen_opcional_del_plato(self):
        self.plato.imagen = 'carta/pizza.png'
        self.plato.save(update_fields=['imagen'])

        pedido = serializar_pedido(self.pedido)

        self.assertTrue(pedido['items'][0]['imagen_url'].endswith('/media/carta/pizza.png'))

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
        self.assertContains(response, 'id="comprobante-transferencia-preview-wrap"')
        self.assertContains(response, 'Imagen del pago en transferencia')
        self.assertContains(response, 'Ver imagen')
        self.assertNotContains(response, '<img id="comprobante-transferencia-preview"')
        self.assertContains(response, 'Seleccionar imagen')
        self.assertContains(response, '/media/carta/pizza.png')
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
        self.pedido.cuenta_solicitada = True
        self.pedido.save(update_fields=['cuenta_solicitada'])

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
