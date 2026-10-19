from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from carta.models import Categoria, Plato
from inventario.admin import InsumoAdminForm
from inventario.models import CategoriaInventario, Insumo, MovimientoInventario, RecetaItem, TasaCambio
from inventario.services import descontar_inventario_por_pedido
from pedidos.models import Factura, ItemPedido, Mesa, Pedido
from usuarios.models import ConfiguracionWifi, Turno


class InventarioFlowTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin', password='123456', role='administrador'
        )
        self.client.force_login(self.admin)
        self.categoria = Categoria.objects.create(nombre='Platos principales', orden=1)
        self.insumo_1 = Insumo.objects.create(nombre='Pollo', unidad='kg', stock_actual=10, stock_minimo=2)
        self.insumo_2 = Insumo.objects.create(nombre='Arroz', unidad='kg', stock_actual=10, stock_minimo=2)

    def test_admin_sidebar_links_to_reports(self):
        response = self.client.get(reverse('inventario:administrador'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("usuarios:reportes")}"')
        self.assertContains(response, 'Reportes')
        self.assertContains(response, 'id="nav-wifi"')

    def test_inventory_forms_render_model_length_and_decimal_limits(self):
        response = self.client.get(reverse('inventario:administrador'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maxlength="150"')
        self.assertContains(response, 'step="0.01" min="0" max="999999.99" name="costo"')
        self.assertContains(response, 'step="0.001" min="0" max="99999999.999" name="stock_actual"')

    def test_django_admin_inventory_form_validates_field_limits(self):
        form = InsumoAdminForm()

        self.assertEqual(getattr(form.fields['nombre'], 'max_length'), 150)
        self.assertEqual(getattr(form.fields['precio'], 'max_value'), Decimal('999999.99'))
        self.assertEqual(getattr(form.fields['stock_actual'], 'max_value'), Decimal('99999999.999'))
        self.assertEqual(getattr(form.fields['stock_maximo'], 'max_value'), Decimal('999999999.999'))
        self.assertIsNone(getattr(form.fields['stock_actual'], 'min_value'))
        with self.assertRaises(ValidationError):
            form.fields['precio'].clean('1000000.00')

    def test_admin_can_load_legacy_large_max_stock_value(self):
        insumo = Insumo.objects.create(
            nombre='Límite amplio',
            stock_maximo=Decimal('999999999'),
        )

        insumo.refresh_from_db()

        self.assertEqual(insumo.stock_maximo, Decimal('999999999.000'))

    def test_django_admin_changelist_loads_large_max_stock_value(self):
        Insumo.objects.create(
            nombre='Límite en listado admin',
            stock_maximo=Decimal('999999999'),
        )
        self.admin.is_staff = True
        self.admin.is_superuser = True
        self.admin.save(update_fields=['is_staff', 'is_superuser'])

        response = self.client.get(reverse('admin:inventario_insumo_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '999999999.000')

    def test_admin_can_create_a_category_from_configuration(self):
        response = self.client.post(
            reverse('inventario:categoria_crear'),
            {'nombre': 'Bebidas'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Categoria.objects.filter(nombre='Bebidas').exists())

    def test_admin_can_configure_wifi_and_view_its_qr(self):
        response = self.client.post(
            reverse('inventario:configurar_wifi'),
            {'ssid': 'WiFi del local', 'password': 'clave-segura'},
        )

        self.assertRedirects(response, f'{reverse("inventario:administrador")}#wifi')
        configuracion = ConfiguracionWifi.objects.get(pk=1)
        self.assertEqual(configuracion.ssid, 'WiFi del local')
        self.assertEqual(configuracion.password, 'clave-segura')

        response = self.client.get(
            reverse('inventario:administrador'),
            HTTP_HOST='192.168.1.25:8765',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="nav-wifi"')
        self.assertContains(response, 'id="admin-view-wifi"')
        self.assertContains(response, 'alt="Código QR para conectarse a la red Wi-Fi WiFi del local"')
        self.assertContains(response, 'alt="Código QR para abrir el sistema en http://192.168.1.25:8765/"')
        self.assertContains(response, 'http://192.168.1.25:8765/')
        self.assertContains(response, 'data:image/png;base64,')
        self.assertContains(response, 'id="wifi-qr-image"')
        self.assertContains(response, "descargarQrJpg('wifi-qr-image', 'qr-wifi.jpg')")
        self.assertContains(response, 'onclick="imprimirQr(\'wifi\')"')
        self.assertContains(response, 'id="sistema-qr-image"')
        self.assertContains(response, "descargarQrJpg('sistema-qr-image', 'qr-sistema.jpg')")
        self.assertContains(response, 'onclick="imprimirQr(\'sistema\')"')
        self.assertEqual(response.content.count(b'Imprimir QR</button>'), 2)

    def test_admin_cannot_save_a_wifi_password_shorter_than_eight_characters(self):
        response = self.client.post(
            reverse('inventario:configurar_wifi'),
            {'ssid': 'WiFi del local', 'password': 'corta'},
        )

        self.assertRedirects(response, f'{reverse("inventario:administrador")}#wifi')
        self.assertFalse(ConfiguracionWifi.objects.exists())

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
        self.assertContains(response, 'id="carta-header-nav"')
        self.assertContains(response, 'data-carta-tab="platos"')
        self.assertContains(response, 'Platos y productos')
        self.assertContains(response, 'data-carta-tab="publicada"')
        self.assertContains(response, 'Carta publicada')
        self.assertContains(response, 'function mostrarSeccionCarta(seccion)')
        self.assertContains(response, 'panel.hidden = panel.id !== `carta-${seccionValida}`')
        self.assertContains(response, 'history.replaceState(null, \'\', hashSeccion)')
        self.assertContains(response, "hashInicial.includes('carta')")
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
        self.assertEqual(response.context['insumos_por_clasificar'].paginator.count, 2)

    def test_admin_paginates_inventory_and_complete_movement_history(self):
        for index in range(26):
            Insumo.objects.create(
                nombre=f'Producto de cocina {index:02}',
                tipo_inventario='cocina',
            )
            MovimientoInventario.objects.create(
                insumo=self.insumo_1,
                tipo_inventario='cocina',
                producto_nombre=f'Movimiento {index:02}',
                tipo='entrada',
                cantidad=1,
                stock_anterior=10,
                stock_posterior=11,
            )

        response = self.client.get(reverse('inventario:administrador'), {
            'cocina_page': 2,
            'movimientos_page': 2,
        })

        self.assertEqual(response.status_code, 200)
        movimientos_primera_pagina = response.context['movimientos']
        self.assertEqual(movimientos_primera_pagina.paginator.per_page, 10)
        self.assertEqual(len(movimientos_primera_pagina), 10)

        response = self.client.get(reverse('inventario:administrador'), {
            'cocina_page': 2,
            'movimientos_page': 2,
        })
        self.assertEqual(response.status_code, 200)
        inventario_cocina = response.context['inventarios'][0]['insumos']
        self.assertEqual(inventario_cocina.number, 2)
        self.assertEqual(inventario_cocina.paginator.count, 26)
        self.assertEqual(len(inventario_cocina), 1)
        movimientos = response.context['movimientos']
        self.assertEqual(movimientos.number, 2)
        self.assertEqual(movimientos.paginator.count, 26)
        self.assertEqual(len(movimientos), 10)
        self.assertContains(response, '?movimientos_page=1')

    def test_admin_menu_has_text_category_and_low_stock_filters(self):
        plato_stock_bajo = Plato.objects.create(
            categoria=self.categoria,
            nombre='Sopa de pollo',
            descripcion='Sopa especial',
            precio='12.00',
        )
        plato_stock_normal = Plato.objects.create(
            categoria=self.categoria,
            nombre='Arroz blanco',
            descripcion='Acompañamiento',
            precio='8.00',
        )
        Insumo.objects.filter(pk=self.insumo_1.pk).update(stock_actual=1)
        RecetaItem.objects.create(plato=plato_stock_bajo, insumo=self.insumo_1)
        RecetaItem.objects.create(plato=plato_stock_normal, insumo=self.insumo_2)

        response = self.client.get(reverse('inventario:administrador'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="carta-buscar-plato"')
        self.assertContains(response, 'id="carta-filtrar-categoria"')
        self.assertContains(response, 'id="carta-solo-stock-bajo"')
        self.assertContains(
            response,
            f'data-categoria="{self.categoria.pk}" data-stock-bajo="true"',
        )
        self.assertContains(
            response,
            f'data-categoria="{self.categoria.pk}" data-stock-bajo="false"',
        )
        self.assertContains(response, 'filtrarPlatosCarta')

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

    def test_restaurant_inventory_item_rejects_price_outside_decimal_field_range(self):
        response = self.client.post(
            reverse('inventario:insumo_crear'),
            {
                'tipo_inventario': 'restaurante',
                'nombre': 'Producto con precio fuera de rango',
                'unidad': 'unidad',
                'precio': '1234567.00',
                'stock_actual': '2',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Insumo.objects.filter(nombre='Producto con precio fuera de rango').exists())
        self.assertContains(response, 'costo y precio, hasta 6 enteros y 2 decimales')

    def test_inventory_edit_rejects_cost_outside_decimal_field_range(self):
        response = self.client.post(
            reverse('inventario:insumo_editar', args=[self.insumo_1.pk]),
            {
                'tipo_inventario': 'cocina',
                'nombre': self.insumo_1.nombre,
                'unidad': self.insumo_1.unidad,
                'stock_actual': '10',
                'stock_minimo': '2',
                'stock_maximo': '0',
                'costo': '1000000.00',
                'precio': '0',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.insumo_1.refresh_from_db()
        self.assertEqual(self.insumo_1.costo, Decimal('0'))
        self.assertContains(response, 'costo y precio, hasta 6 enteros y 2 decimales')

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


class DemoDataCommandTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin', password='123456', role='administrador'
        )
        self.mesa = Mesa.objects.create(numero=1, activa=True, abierta=True)
        self.tasa = TasaCambio.objects.create(valor='385.00')

    def test_demo_command_requires_confirmation_and_dry_run_is_safe(self):
        categoria = Categoria.objects.create(nombre='Categoría que se conserva en dry-run')

        with self.assertRaises(CommandError):
            call_command('cargar_datos_demo')
        call_command('cargar_datos_demo', dry_run=True)

        self.assertTrue(Categoria.objects.filter(pk=categoria.pk).exists())
        self.assertTrue(Mesa.objects.get(pk=self.mesa.pk).abierta)

    def test_demo_command_replaces_operational_data_and_creates_full_history(self):
        categoria_anterior = Categoria.objects.create(nombre='Categoría anterior')
        plato_anterior = Plato.objects.create(
            categoria=categoria_anterior,
            nombre='Plato anterior',
            precio='100.00',
        )
        pedido_anterior = Pedido.objects.create(
            mesa=self.mesa,
            estado='cerrado',
            modalidad='mesa',
            sesion_id=self.mesa.sesion_id,
        )
        ItemPedido.objects.create(pedido=pedido_anterior, plato=plato_anterior, cantidad=1)
        Factura.objects.create(
            pedido=pedido_anterior,
            mesa_numero=self.mesa.numero,
            forma_pago='efectivo_cup',
            total_cup='100.00',
            monto_efectivo_cup='100.00',
            items_snapshot=[{
                'plato': plato_anterior.nombre,
                'cantidad': 1,
                'precio_unit': '100.00',
                'subtotal': '100.00',
            }],
        )
        Turno.objects.create(estado='cerrado')

        call_command('cargar_datos_demo', confirm=True, verbosity=0)

        hoy = timezone.localdate()
        self.assertTrue(get_user_model().objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(TasaCambio.objects.filter(pk=self.tasa.pk, valor='385.00').exists())
        self.assertTrue(Mesa.objects.filter(pk=self.mesa.pk).exists())
        self.assertTrue(Mesa.objects.get(pk=self.mesa.pk).abierta)
        self.assertFalse(Categoria.objects.filter(nombre='Categoría anterior').exists())
        self.assertFalse(Plato.objects.filter(nombre='Plato anterior').exists())
        self.assertEqual(Turno.objects.filter(estado='cerrado').count(), 3)
        self.assertEqual(Turno.objects.filter(estado='abierto', fecha=hoy).count(), 1)
        self.assertEqual(Factura.objects.count(), 18)
        self.assertGreaterEqual(Pedido.objects.filter(estado='cerrado').count(), 18)
        self.assertFalse(
            Pedido.objects.filter(
                estado='cerrado',
                items__plato__categoria__nombre='Bebidas',
            ).exclude(destino='barra').exists()
        )
        self.assertEqual(
            Pedido.objects.filter(
                creado_en__date=hoy,
                items__plato__nombre='Refresco embotellado',
                destino='barra',
            ).count(),
            1,
        )
        for factura in Factura.objects.all():
            total_snapshot = sum(
                Decimal(item['subtotal']) for item in factura.items_snapshot
            )
            self.assertEqual(total_snapshot, factura.total_cup)
        self.assertEqual(Pedido.objects.filter(creado_en__date=hoy).count(), 3)
        self.assertEqual(Categoria.objects.count(), 5)
        self.assertEqual(Plato.objects.count(), 27)
        self.assertEqual(
            Plato.objects.filter(disponible=True, receta__isnull=False).distinct().count(),
            27,
        )
        self.assertTrue(Plato.objects.filter(nombre='Red Bull').exists())
        self.assertTrue(Plato.objects.filter(nombre='Mojito').exists())
        self.assertTrue(Plato.objects.filter(nombre='Botella de ron añejo').exists())
        self.assertTrue(
            Insumo.objects.filter(
                nombre='Red Bull',
                tipo_inventario='restaurante',
                categoria_inventario__nombre='Bebidas frías',
            ).exists()
        )
        self.assertTrue(
            Insumo.objects.filter(
                nombre='Flan porción',
                tipo_inventario='restaurante',
                categoria_inventario__nombre='Postres',
            ).exists()
        )
        self.assertTrue(
            RecetaItem.objects.filter(
                plato__nombre='Mojito',
                insumo__nombre='Ron añejo',
                cantidad='50.000',
            ).exists()
        )
        self.assertTrue(
            RecetaItem.objects.filter(
                plato__nombre='Flan de la casa',
                insumo__nombre='Flan porción',
            ).exists()
        )
        self.assertEqual(
            set(Factura.objects.values_list('creado_en__date', flat=True)),
            {
                hoy - timedelta(days=1),
                hoy - timedelta(days=2),
                hoy - timedelta(days=3),
            },
        )
        for turno in Turno.objects.filter(estado='cerrado'):
            self.assertEqual(turno.cantidad_facturas, 6)
            self.assertGreater(turno.total_general_cup, Decimal('0'))
        self.assertGreater(MovimientoInventario.objects.filter(tipo='salida').count(), 0)

        call_command('cargar_datos_demo', confirm=True, verbosity=0)
        self.assertEqual(Factura.objects.count(), 18)
        self.assertEqual(Turno.objects.count(), 4)
