from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from carta.models import Categoria, Plato
from inventario.models import Insumo, MovimientoInventario, RecetaItem
from inventario.services import descontar_inventario_por_pedido
from pedidos.models import ItemPedido, Mesa, Pedido


class InventarioFlowTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin', password='123456', role='administrador'
        )
        self.client.force_login(self.admin)
        self.categoria = Categoria.objects.create(nombre='Platos principales', orden=1)
        self.insumo_1 = Insumo.objects.create(nombre='Pollo', unidad='kg', stock_actual=10, stock_minimo=2)
        self.insumo_2 = Insumo.objects.create(nombre='Arroz', unidad='kg', stock_actual=10, stock_minimo=2)

    def test_admin_can_create_a_category_from_configuration(self):
        response = self.client.post(
            reverse('inventario:categoria_crear'),
            {'nombre': 'Bebidas'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Categoria.objects.filter(nombre='Bebidas').exists())

    def test_carta_usa_el_precio_de_venta_del_inventario(self):
        self.insumo_1.precio = '18.50'
        self.insumo_1.save()
        plato = Plato.objects.create(
            nombre='Pollo',
            categoria=self.categoria,
            descripcion='Producto sincronizado',
            precio='1.00',
        )

        response = self.client.get(reverse('inventario:administrador'))

        plato_context = next(item for item in response.context['platos'] if item.pk == plato.pk)
        self.assertEqual(plato_context.precio_inventario, 18.50)
        self.client.post(reverse('inventario:plato_editar', args=[plato.pk]), {
            'nombre': 'Pollo',
            'categoria': self.categoria.id,
            'descripcion': 'Producto actualizado',
            'precio': '99.00',
            'disponible': 'on',
        })
        plato.refresh_from_db()
        self.assertEqual(plato.precio, 18.50)

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

    def test_inventory_item_is_synced_to_menu_product(self):
        categoria = Categoria.objects.create(nombre='Bebidas', orden=2)

        response = self.client.post(
            reverse('inventario:insumo_crear'),
            {
                'nombre': 'Coca-Cola',
                'unidad': 'unidad',
                'categoria': categoria.id,
                'precio': '12.50',
                'stock_actual': '20',
                'stock_minimo': '5',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        insumo = Insumo.objects.get(nombre='Coca-Cola')
        self.assertEqual(insumo.categoria, categoria)
        self.assertEqual(insumo.precio, 12.50)
        self.assertEqual(insumo.costo, 0)
        self.assertEqual(insumo.stock_maximo, 0)
        movimiento = MovimientoInventario.objects.get(insumo=insumo)
        self.assertEqual(movimiento.tipo, 'entrada')
        self.assertEqual(movimiento.cantidad, 20)
        self.assertEqual(movimiento.stock_anterior, 0)
        self.assertEqual(movimiento.stock_posterior, 20)
        self.assertEqual(movimiento.usuario, self.admin)
        plato = Plato.objects.get(nombre='Coca-Cola')
        self.assertEqual(plato.categoria, categoria)
        self.assertEqual(plato.precio, 12.50)
        self.assertTrue(plato.disponible)

    def test_admin_puede_registrar_una_entrada_manual(self):
        response = self.client.post(reverse('inventario:movimiento_crear'), {
            'insumo_id': self.insumo_1.id,
            'tipo': 'entrada',
            'cantidad': '4.50',
            'motivo': 'Compra semanal',
        })

        self.assertEqual(response.status_code, 302)
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, 14.50)
        movimiento = MovimientoInventario.objects.get(insumo=self.insumo_1)
        self.assertEqual(movimiento.motivo, 'Compra semanal')
        self.assertEqual(movimiento.stock_anterior, 10)
        self.assertEqual(movimiento.stock_posterior, 14.50)

    def test_salida_automatica_guarda_antes_despues_y_responsable(self):
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Pollo servido',
            descripcion='Plato de prueba',
            precio=25,
        )
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='0.50')
        mesa = Mesa.objects.create(numero=1)
        pedido = Pedido.objects.create(mesa=mesa)
        ItemPedido.objects.create(pedido=pedido, plato=plato, cantidad=2)

        descontar_inventario_por_pedido(pedido, usuario=self.admin)

        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, 9)
        movimiento = MovimientoInventario.objects.get(insumo=self.insumo_1)
        self.assertEqual(movimiento.tipo, 'salida')
        self.assertEqual(movimiento.cantidad, 1)
        self.assertEqual(movimiento.stock_anterior, 10)
        self.assertEqual(movimiento.stock_posterior, 9)
        self.assertEqual(movimiento.usuario, self.admin)
        self.assertIn(f'Pedido #{pedido.pk}', movimiento.motivo)
