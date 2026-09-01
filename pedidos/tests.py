from django.test import TestCase

from carta.models import Categoria, Plato
from pedidos.consumers import MeseraConsumer, serializar_cuenta, serializar_factura
from pedidos.models import Factura, ItemPedido, Mesa, Pedido


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

    def test_caja_registra_factura_con_items_y_pdf(self):
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

        resultado = serializar_factura(factura)

        self.assertIsNotNone(resultado)
        self.assertIn('items_snapshot', resultado)
        self.assertEqual(resultado['items_snapshot'][0]['plato'], self.plato.nombre)
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
