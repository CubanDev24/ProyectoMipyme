from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from carta.qr import generar_qr_wifi
from pedidos.models import Mesa


class WifiQrTests(TestCase):
    def test_wifi_qr_uses_escaped_wpa_credentials(self):
        with patch('carta.qr.qrcode.make') as make_qr:
            make_qr.return_value.save.side_effect = lambda buffer, format: buffer.write(b'png')

            resultado = generar_qr_wifi(r'Red;Local:5', r'clave;segura:2026')

        make_qr.assert_called_once_with(r'WIFI:T:WPA;S:Red\;Local\:5;P:clave\;segura\:2026;;')
        self.assertEqual(resultado, 'data:image/png;base64,cG5n')

    def test_wifi_qr_supports_an_open_network(self):
        with patch('carta.qr.qrcode.make') as make_qr:
            make_qr.return_value.save.side_effect = lambda buffer, format: buffer.write(b'png')

            generar_qr_wifi('Red abierta', '')

        make_qr.assert_called_once_with('WIFI:T:nopass;S:Red abierta;;')


class CartaQrTests(TestCase):
    def test_qr_mesa_generates_a_page_for_the_table(self):
        Mesa.objects.create(numero=1, activa=True, abierta=True)

        response = self.client.get(reverse('carta:qr_mesa', args=[1]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mesa 1')
        self.assertContains(response, 'QR')
        self.assertContains(response, 'class="qr-mesa-card"')
        self.assertContains(response, 'Escanea este código con la cámara de tu teléfono')
        self.assertContains(response, 'css/qr-mesa.css')
        self.assertContains(response, 'href="http://testserver/carta/mesa/1/"')
        self.assertTrue(response.headers.get('X-Frame-Options') in (None, 'ALLOWALL'))

    def test_qr_y_carta_muestran_la_etiqueta_de_exteriores(self):
        mesa = Mesa.objects.create(
            numero=90,
            zona='exteriores',
            numero_zona=1,
            activa=True,
            abierta=True,
        )

        qr_response = self.client.get(reverse('carta:qr_mesa', args=[mesa.numero]))
        carta_response = self.client.get(reverse('carta:carta', args=[mesa.numero]))

        self.assertContains(qr_response, 'Exteriores 1')
        self.assertContains(carta_response, 'Exteriores 1')
        self.assertContains(carta_response, 'id="placedOrdersTotal"')
        self.assertContains(carta_response, 'id="placedOrdersTotalValue"')
        self.assertContains(carta_response, 'id="placedOrdersCount"')
        self.assertContains(carta_response, 'id="placedItemsCount"')
        self.assertContains(carta_response, 'Pedido #${numeroVisiblePedido(pedido)}')
        self.assertNotContains(carta_response, 'id="btnFacturaDirecta"')
        self.assertNotContains(carta_response, 'Ver y Descargar Factura')
