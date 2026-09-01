from django.test import TestCase
from django.urls import reverse

from pedidos.models import Mesa


class CartaQrTests(TestCase):
    def test_qr_mesa_generates_a_page_for_the_table(self):
        Mesa.objects.create(numero=1, activa=True, abierta=True)

        response = self.client.get(reverse('carta:qr_mesa', args=[1]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mesa 1')
        self.assertContains(response, 'QR')
        self.assertTrue(response.headers.get('X-Frame-Options') in (None, 'ALLOWALL'))
