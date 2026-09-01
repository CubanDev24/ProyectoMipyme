from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from carta.models import Categoria, Plato
from inventario.models import Insumo, RecetaItem


class InventarioFlowTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin', password='123456', role='administrador'
        )
        self.client.force_login(self.admin)
        self.categoria = Categoria.objects.create(nombre='Platos principales', orden=1)
        self.insumo_1 = Insumo.objects.create(nombre='Pollo', unidad='kg', stock_actual=10, stock_minimo=2)
        self.insumo_2 = Insumo.objects.create(nombre='Arroz', unidad='kg', stock_actual=10, stock_minimo=2)

    def test_admin_can_create_a_dish_with_multiple_inventory_items(self):
        response = self.client.post(
            reverse('inventario:plato_crear'),
            {
                'categoria': self.categoria.id,
                'nombre': 'Arroz con pollo',
                'descripcion': 'Plato del día',
                'precio': '25.00',
                'disponible': 'on',
                'insumo_id': [str(self.insumo_1.id), str(self.insumo_2.id)],
                'cantidad': ['0.5', '0.25'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        plato = Plato.objects.get(nombre='Arroz con pollo')
        self.assertEqual(plato.receta.count(), 2)
        self.assertTrue(RecetaItem.objects.filter(plato=plato, insumo=self.insumo_1).exists())
        self.assertTrue(RecetaItem.objects.filter(plato=plato, insumo=self.insumo_2).exists())
