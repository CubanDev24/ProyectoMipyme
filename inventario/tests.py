from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from carta.models import Categoria, Plato
from inventario.models import CategoriaInventario, Insumo, MovimientoInventario, RecetaItem
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

    def test_admin_can_rename_a_category(self):
        response = self.client.post(
            reverse('inventario:categoria_editar', args=[self.categoria.pk]),
            {'nombre': 'Entrantes'},
        )

        self.assertEqual(response.status_code, 302)
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.nombre, 'Entrantes')

    def test_category_rename_rejects_empty_or_duplicate_names(self):
        otra_categoria = Categoria.objects.create(nombre='Bebidas', orden=2)

        for nombre in ('', '  ', 'BEBIDAS'):
            with self.subTest(nombre=nombre):
                response = self.client.post(
                    reverse('inventario:categoria_editar', args=[self.categoria.pk]),
                    {'nombre': nombre},
                )
                self.assertEqual(response.status_code, 302)
                self.categoria.refresh_from_db()
                self.assertEqual(self.categoria.nombre, 'Platos principales')

        self.assertEqual(otra_categoria.nombre, 'Bebidas')

    def test_admin_can_delete_a_menu_category_without_deleting_its_dishes(self):
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Pollo al horno',
            precio='22.00',
            disponible=True,
        )
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='0.500')

        response = self.client.post(
            reverse('inventario:categoria_eliminar', args=[self.categoria.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Categoria.objects.filter(pk=self.categoria.pk).exists())
        plato.refresh_from_db()
        self.assertIsNone(plato.categoria)
        self.assertTrue(plato.receta.exists())
        carta_response = self.client.get(reverse('carta:carta', args=[1]))
        self.assertContains(carta_response, 'Pollo al horno')
        self.assertContains(carta_response, 'Sin categoría')

    def test_inventory_categories_are_separate_by_area(self):
        cocina = CategoriaInventario.objects.create(tipo='cocina', nombre='Carnes')
        restaurante = CategoriaInventario.objects.create(tipo='restaurante', nombre='Carnes')

        self.assertNotEqual(cocina.pk, restaurante.pk)
        self.assertEqual(CategoriaInventario.objects.filter(nombre='Carnes').count(), 2)

    def test_admin_can_create_and_rename_an_inventory_category(self):
        response = self.client.post(
            reverse('inventario:categoria_inventario_crear', args=['cocina']),
            {'nombre': 'Vegetales'},
        )
        self.assertEqual(response.status_code, 302)
        categoria = CategoriaInventario.objects.get(tipo='cocina', nombre='Vegetales')

        response = self.client.post(
            reverse('inventario:categoria_inventario_editar', args=['cocina', categoria.pk]),
            {'nombre': 'Vegetales frescos'},
        )

        self.assertEqual(response.status_code, 302)
        categoria.refresh_from_db()
        self.assertEqual(categoria.nombre, 'Vegetales frescos')

    def test_admin_can_delete_inventory_categories_without_deleting_products(self):
        for tipo, insumo in (
            ('cocina', self.insumo_1),
            ('restaurante', self.insumo_2),
        ):
            with self.subTest(tipo=tipo):
                categoria = CategoriaInventario.objects.create(
                    tipo=tipo,
                    nombre=f'Categoría {tipo}',
                )
                insumo.tipo_inventario = tipo
                insumo.categoria_inventario = categoria
                insumo.save(update_fields=['tipo_inventario', 'categoria_inventario'])

                response = self.client.post(
                    reverse(
                        'inventario:categoria_inventario_eliminar',
                        args=[tipo, categoria.pk],
                    ),
                )

                self.assertEqual(response.status_code, 302)
                self.assertFalse(CategoriaInventario.objects.filter(pk=categoria.pk).exists())
                insumo.refresh_from_db()
                self.assertIsNone(insumo.categoria_inventario)
                self.assertEqual(insumo.tipo_inventario, tipo)
                self.assertEqual(insumo.stock_actual, Decimal('10'))

    def test_admin_inventory_page_shows_separate_areas_and_legacy_products(self):
        categoria_cocina = CategoriaInventario.objects.create(
            tipo='cocina',
            nombre='Carnes',
        )
        categoria_restaurante = CategoriaInventario.objects.create(
            tipo='restaurante',
            nombre='Bebidas',
        )
        response = self.client.get(reverse('inventario:administrador'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inventario de Cocina')
        self.assertContains(response, 'Inventario de Restaurante')
        self.assertContains(response, 'id="nav-cocina"')
        self.assertContains(response, 'id="nav-restaurante"')
        self.assertContains(response, 'id="admin-view-cocina"')
        self.assertContains(response, 'id="admin-view-restaurante"')
        self.assertContains(response, 'id="nav-configuracion"')
        self.assertContains(response, 'id="nav-carta"')
        self.assertContains(response, 'id="admin-view-cocina" class="admin-view admin-view-hidden"')
        self.assertContains(response, 'id="admin-view-restaurante" class="admin-view admin-view-hidden"')
        self.assertContains(response, 'Productos por clasificar')
        self.assertContains(
            response,
            reverse('inventario:categoria_eliminar', args=[self.categoria.pk]),
        )
        self.assertContains(
            response,
            reverse('inventario:categoria_inventario_eliminar', args=['cocina', categoria_cocina.pk]),
        )
        self.assertContains(
            response,
            reverse('inventario:categoria_inventario_eliminar', args=['restaurante', categoria_restaurante.pk]),
        )
        self.assertEqual(response.context['insumos_por_clasificar'].count(), 2)

    def test_admin_can_classify_legacy_item_as_kitchen_without_losing_existing_data(self):
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Pollo',
            descripcion='Plato vinculado antes de clasificar',
            precio='22.00',
        )
        Insumo.objects.filter(pk=self.insumo_1.pk).update(categoria=self.categoria)
        self.insumo_1.refresh_from_db()
        receta = RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='0.50')
        categoria = CategoriaInventario.objects.create(tipo='cocina', nombre='Carnes')

        response = self.client.post(
            reverse('inventario:insumo_editar', args=[self.insumo_1.pk]),
            {
                'nombre': 'Pollo',
                'tipo_inventario': 'cocina',
                'categoria_inventario': categoria.pk,
                'unidad': 'kg',
                'stock_actual': '10',
                'stock_minimo': '2',
                'stock_maximo': '0',
                'costo': '0',
                'precio': '0',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.tipo_inventario, 'cocina')
        self.assertEqual(self.insumo_1.categoria_inventario, categoria)
        self.assertEqual(self.insumo_1.categoria, self.categoria)
        self.assertEqual(self.insumo_1.stock_actual, Decimal('10'))
        self.assertTrue(RecetaItem.objects.filter(pk=receta.pk).exists())
        plato.refresh_from_db()
        self.assertEqual(plato.precio, Decimal('22.00'))

    def test_admin_can_classify_legacy_item_as_restaurant_without_creating_a_menu_item(self):
        categoria_inventario = CategoriaInventario.objects.create(
            tipo='restaurante',
            nombre='Bebidas',
        )

        response = self.client.post(
            reverse('inventario:insumo_editar', args=[self.insumo_1.pk]),
            {
                'nombre': 'Pollo',
                'tipo_inventario': 'restaurante',
                'categoria_inventario': categoria_inventario.pk,
                'unidad': 'kg',
                'stock_actual': '10',
                'stock_minimo': '2',
                'stock_maximo': '0',
                'costo': '0',
                'precio': '18.50',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.tipo_inventario, 'restaurante')
        self.assertEqual(self.insumo_1.categoria_inventario, categoria_inventario)
        self.assertIsNone(self.insumo_1.categoria)
        self.assertFalse(Plato.objects.filter(nombre='Pollo').exists())

    def test_admin_cannot_assign_inventory_category_from_another_area(self):
        categoria = CategoriaInventario.objects.create(tipo='restaurante', nombre='Bebidas')

        response = self.client.post(
            reverse('inventario:insumo_editar', args=[self.insumo_1.pk]),
            {
                'nombre': self.insumo_1.nombre,
                'tipo_inventario': 'cocina',
                'categoria_inventario': categoria.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.insumo_1.refresh_from_db()
        self.assertIsNone(self.insumo_1.tipo_inventario)
        self.assertIsNone(self.insumo_1.categoria_inventario)

    def test_menu_item_price_is_independent_from_inventory_product_price(self):
        plato = Plato.objects.create(
            nombre='Pollo',
            categoria=self.categoria,
            descripcion='Plato administrado desde Carta',
            precio='22.00',
            disponible=False,
        )
        self.insumo_1.nombre = 'Pollo'
        self.insumo_1.precio = Decimal('18.50')
        self.insumo_1.save()

        response = self.client.get(reverse('inventario:administrador'))

        plato_context = next(item for item in response.context['platos'] if item.pk == plato.pk)
        self.assertEqual(plato_context.precio, Decimal('22.00'))
        self.client.post(reverse('inventario:plato_editar', args=[plato.pk]), {
            'nombre': 'Pollo',
            'categoria': self.categoria.id,
            'descripcion': 'Producto actualizado',
            'precio': '99.00',
        })
        plato.refresh_from_db()
        self.assertEqual(plato.precio, Decimal('99.00'))

    def test_admin_can_create_a_dish_with_multiple_inventory_items(self):
        Insumo.objects.filter(pk__in=[self.insumo_1.pk, self.insumo_2.pk]).update(
            tipo_inventario='cocina',
        )
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

    def test_restaurant_inventory_item_does_not_create_or_change_menu_item(self):
        Plato.objects.create(
            categoria=self.categoria,
            nombre='Coca-Cola',
            descripcion='Plato manual',
            precio='25.00',
            disponible=True,
        )
        categoria_inventario = CategoriaInventario.objects.create(
            tipo='restaurante',
            nombre='Bebidas envasadas',
        )

        response = self.client.post(
            reverse('inventario:insumo_crear'),
            {
                'tipo_inventario': 'restaurante',
                'nombre': 'Coca-Cola',
                'unidad': 'unidad',
                'categoria_inventario': categoria_inventario.pk,
                'precio': '12.50',
                'stock_actual': '20',
                'stock_minimo': '5',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        insumo = Insumo.objects.get(nombre='Coca-Cola')
        self.assertEqual(insumo.tipo_inventario, 'restaurante')
        self.assertEqual(insumo.categoria_inventario, categoria_inventario)
        self.assertIsNone(insumo.categoria)
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
        self.assertEqual(plato.descripcion, 'Plato manual')
        self.assertEqual(plato.precio, Decimal('25.00'))
        self.assertTrue(plato.disponible)

    def test_new_available_menu_item_requires_a_valid_inventory_recipe(self):
        response = self.client.post(
            reverse('inventario:plato_crear'),
            {
                'categoria': self.categoria.pk,
                'nombre': 'Plato sin receta',
                'precio': '15.00',
                'disponible': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Plato.objects.filter(nombre='Plato sin receta').exists())

    def test_menu_recipe_accepts_active_products_from_both_inventory_areas(self):
        self.insumo_1.tipo_inventario = 'cocina'
        self.insumo_1.save(update_fields=['tipo_inventario'])
        self.insumo_2.tipo_inventario = 'restaurante'
        self.insumo_2.save(update_fields=['tipo_inventario'])

        response = self.client.post(
            reverse('inventario:plato_crear'),
            {
                'categoria': self.categoria.pk,
                'nombre': 'Plato mixto',
                'precio': '35.00',
                'disponible': 'on',
                'insumo_id[]': [str(self.insumo_1.pk), str(self.insumo_2.pk)],
                'cantidad[]': ['0.125', '1'],
            },
        )

        self.assertEqual(response.status_code, 302)
        plato = Plato.objects.get(nombre='Plato mixto')
        self.assertEqual(plato.receta.count(), 2)
        self.assertEqual(plato.receta.get(insumo=self.insumo_1).cantidad, Decimal('0.125'))
        self.assertEqual(plato.receta.get(insumo=self.insumo_2).cantidad, Decimal('1'))

    def test_admin_can_replace_a_plato_recipe(self):
        self.insumo_1.tipo_inventario = 'cocina'
        self.insumo_1.save(update_fields=['tipo_inventario'])
        self.insumo_2.tipo_inventario = 'restaurante'
        self.insumo_2.save(update_fields=['tipo_inventario'])
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Plato editable',
            precio='20.00',
            disponible=True,
        )
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='1.000')

        response = self.client.post(
            reverse('inventario:plato_editar', args=[plato.pk]),
            {
                'nombre': 'Plato editable',
                'categoria': self.categoria.pk,
                'precio': '20.00',
                'disponible': 'on',
                'receta_enviada': '1',
                'insumo_id[]': [str(self.insumo_2.pk)],
                'cantidad[]': ['0.750'],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(plato.receta.values_list('insumo_id', 'cantidad')),
            [(self.insumo_2.pk, Decimal('0.750'))],
        )

    def test_admin_cannot_publish_a_recipe_with_inactive_inventory(self):
        self.insumo_1.tipo_inventario = 'cocina'
        self.insumo_1.activo = False
        self.insumo_1.save(update_fields=['tipo_inventario', 'activo'])

        response = self.client.post(
            reverse('inventario:plato_crear'),
            {
                'categoria': self.categoria.pk,
                'nombre': 'Plato con ingrediente inactivo',
                'precio': '15.00',
                'disponible': 'on',
                'insumo_id[]': [str(self.insumo_1.pk)],
                'cantidad[]': ['1'],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Plato.objects.filter(nombre='Plato con ingrediente inactivo').exists())

    def test_kitchen_inventory_item_does_not_create_a_menu_product(self):
        categoria = CategoriaInventario.objects.create(tipo='cocina', nombre='Carnes')

        response = self.client.post(
            reverse('inventario:insumo_crear'),
            {
                'tipo_inventario': 'cocina',
                'nombre': 'Carne para cocina',
                'categoria_inventario': categoria.pk,
                'unidad': 'kg',
                'stock_actual': '8',
            },
        )

        self.assertEqual(response.status_code, 302)
        insumo = Insumo.objects.get(nombre='Carne para cocina')
        self.assertEqual(insumo.tipo_inventario, 'cocina')
        self.assertEqual(insumo.categoria_inventario, categoria)
        self.assertFalse(Plato.objects.filter(nombre='Carne para cocina').exists())
        movimiento = MovimientoInventario.objects.get(insumo=insumo)
        self.assertEqual(movimiento.tipo_inventario, 'cocina')

    def test_creating_inventory_item_rejects_category_from_other_area(self):
        categoria = CategoriaInventario.objects.create(tipo='restaurante', nombre='Bebidas')

        response = self.client.post(
            reverse('inventario:insumo_crear'),
            {
                'tipo_inventario': 'cocina',
                'nombre': 'Producto inválido',
                'categoria_inventario': categoria.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Insumo.objects.filter(nombre='Producto inválido').exists())

    def test_admin_puede_registrar_una_entrada_manual(self):
        self.insumo_1.tipo_inventario = 'cocina'
        self.insumo_1.save(update_fields=['tipo_inventario'])
        response = self.client.post(reverse('inventario:movimiento_crear'), {
            'insumo_id': self.insumo_1.id,
            'tipo': 'entrada',
            'cantidad': '4.50',
            'motivo': 'Compra semanal',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/administrador/#inventario-cocina')
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, 14.50)
        movimiento = MovimientoInventario.objects.get(insumo=self.insumo_1)
        self.assertEqual(movimiento.motivo, 'Compra semanal')
        self.assertEqual(movimiento.stock_anterior, 10)
        self.assertEqual(movimiento.stock_posterior, 14.50)

    def test_admin_puede_registrar_merma_por_rotura(self):
        response = self.client.post(reverse('inventario:movimiento_crear'), {
            'insumo_id': self.insumo_1.id,
            'tipo': 'merma',
            'cantidad': '1.25',
            'motivo': 'Botella rota',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/administrador/#movimientos-inventario')
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, Decimal('8.75'))
        movimiento = MovimientoInventario.objects.get(insumo=self.insumo_1)
        self.assertEqual(movimiento.tipo, 'merma')
        self.assertEqual(movimiento.stock_anterior, Decimal('10'))
        self.assertEqual(movimiento.stock_posterior, Decimal('8.75'))
        self.assertEqual(movimiento.motivo, 'Botella rota')

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

    def test_fractional_recipe_deduction_preserves_three_decimal_precision(self):
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Porción fraccionada',
            descripcion='Consumo medido con tres decimales',
            precio=25,
        )
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='0.125')
        mesa = Mesa.objects.create(numero=4)
        pedido = Pedido.objects.create(mesa=mesa)
        ItemPedido.objects.create(pedido=pedido, plato=plato, cantidad=1)

        descontar_inventario_por_pedido(pedido, usuario=self.admin)

        self.insumo_1.refresh_from_db()
        movimiento = MovimientoInventario.objects.get(insumo=self.insumo_1)
        self.assertEqual(self.insumo_1.stock_actual, Decimal('9.875'))
        self.assertEqual(movimiento.cantidad, Decimal('0.125'))
        self.assertEqual(movimiento.stock_posterior, Decimal('9.875'))

    def test_product_without_recipe_is_not_deducted_by_matching_its_name(self):
        self.insumo_1.nombre = 'Coca-Cola'
        self.insumo_1.tipo_inventario = 'restaurante'
        self.insumo_1.save()
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Coca-Cola',
            descripcion='Artículo independiente de carta',
            precio='12.50',
        )
        mesa = Mesa.objects.create(numero=2)
        pedido = Pedido.objects.create(mesa=mesa)
        ItemPedido.objects.create(pedido=pedido, plato=plato, cantidad=3)

        descontar_inventario_por_pedido(pedido, usuario=self.admin)

        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, Decimal('10'))
        self.assertFalse(MovimientoInventario.objects.filter(insumo=self.insumo_1).exists())

    def test_served_dish_deducts_explicit_products_from_both_inventory_areas(self):
        self.insumo_1.tipo_inventario = 'cocina'
        self.insumo_1.save(update_fields=['tipo_inventario'])
        self.insumo_2.tipo_inventario = 'restaurante'
        self.insumo_2.save(update_fields=['tipo_inventario'])
        plato = Plato.objects.create(
            categoria=self.categoria,
            nombre='Plato mixto servido',
            descripcion='Receta con dos áreas',
            precio=35,
        )
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_1, cantidad='0.125')
        RecetaItem.objects.create(plato=plato, insumo=self.insumo_2, cantidad='1.000')
        mesa = Mesa.objects.create(numero=3)
        pedido = Pedido.objects.create(mesa=mesa)
        ItemPedido.objects.create(pedido=pedido, plato=plato, cantidad=2)

        descontar_inventario_por_pedido(pedido, usuario=self.admin)

        self.insumo_1.refresh_from_db()
        self.insumo_2.refresh_from_db()
        self.assertEqual(self.insumo_1.stock_actual, Decimal('9.75'))
        self.assertEqual(self.insumo_2.stock_actual, Decimal('8'))
        self.assertEqual(
            set(MovimientoInventario.objects.values_list('tipo_inventario', flat=True)),
            {'cocina', 'restaurante'},
        )
