from datetime import timedelta
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse

from inventario.models import Insumo, MovimientoInventario
from carta.models import Categoria, Plato
from pedidos.models import Factura, Mesa, Pedido
from usuarios.models import Usuario, Notificacion, configurar_turno, get_turno_abierto, registrar_inicio_turno, cerrar_turno, mesas_del_turno
from usuarios.views import historial_turnos_view, historial_turno_detalle_view


class UsuarioTurnoTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(
            username='admin', password='123456', role='administrador'
        )
        self.mesera = Usuario.objects.create_user(
            username='mesera1', password='123456', role='mesera'
        )
        self.cajera = Usuario.objects.create_user(
            username='cajera1', password='123456', role='cajera'
        )

    def test_registrar_inicio_turno_agrega_usuario_al_turno_actual(self):
        turno = registrar_inicio_turno(self.mesera)

        self.assertIsNotNone(turno)
        self.assertEqual(turno.usuarios.count(), 1)
        self.assertIn(self.mesera, turno.usuarios.all())

    def test_dashboard_muestra_facturas_del_turno_y_excluye_las_anteriores(self):
        turno = registrar_inicio_turno(self.mesera)
        mesa = Mesa.objects.create(numero=1, abierta=True)
        pedido_anterior = Pedido.objects.create(mesa=mesa, estado='cerrado')
        factura_anterior = Factura.objects.create(
            pedido=pedido_anterior,
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('12.00'),
        )
        Factura.objects.filter(pk=factura_anterior.pk).update(
            creado_en=turno.apertura - timedelta(seconds=1)
        )
        pedido_turno = Pedido.objects.create(mesa=mesa, estado='cerrado')
        factura_turno = Factura.objects.create(
            pedido=pedido_turno,
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('25.00'),
            monto_efectivo_cup=Decimal('25.00'),
            cajera_nombre='Cajera de prueba',
            items_snapshot=[{
                'plato': 'Plato de prueba',
                'cantidad': 1,
                'precio_unit': '25.00',
                'subtotal': '25.00',
            }],
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('usuarios:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['facturas_turno']), [factura_turno])
        self.assertContains(response, f'Factura #{factura_turno.pk}')
        self.assertContains(response, 'Plato de prueba')
        self.assertContains(response, 'Inventario perpetuo de ventas')
        self.assertContains(response, 'Saldo inicial')
        self.assertContains(response, 'Merma rotura')
        self.assertContains(response, 'setInterval(actualizarInventarioTurno, 5000)')
        self.assertNotContains(response, f'Factura #{factura_anterior.pk}')

    def test_reporte_inventario_turno_calcula_ventas_entradas_roturas_y_saldos(self):
        turno = registrar_inicio_turno(self.mesera)
        insumo = Insumo.objects.create(nombre='Coca-Cola', stock_actual=Decimal('10'), precio=Decimal('12.50'))
        movimiento_inicial = MovimientoInventario.objects.create(
            insumo=insumo,
            producto_nombre=insumo.nombre,
            tipo='entrada',
            cantidad=Decimal('10'),
            stock_anterior=Decimal('0'),
            stock_posterior=Decimal('10'),
        )
        MovimientoInventario.objects.filter(pk=movimiento_inicial.pk).update(
            creado_en=turno.apertura - timedelta(seconds=1),
        )
        MovimientoInventario.objects.create(
            insumo=insumo,
            producto_nombre=insumo.nombre,
            tipo='entrada',
            cantidad=Decimal('5'),
            stock_anterior=Decimal('10'),
            stock_posterior=Decimal('15'),
        )
        MovimientoInventario.objects.create(
            insumo=insumo,
            producto_nombre=insumo.nombre,
            tipo='salida',
            cantidad=Decimal('2'),
            stock_anterior=Decimal('15'),
            stock_posterior=Decimal('13'),
            motivo='Pedido vendido',
        )
        MovimientoInventario.objects.create(
            insumo=insumo,
            producto_nombre=insumo.nombre,
            tipo='merma',
            cantidad=Decimal('1'),
            stock_anterior=Decimal('13'),
            stock_posterior=Decimal('12'),
            motivo='Botella rota',
        )
        insumo.stock_actual = Decimal('12')
        insumo.save(update_fields=['stock_actual', 'actualizado_en'])
        mesa = Mesa.objects.create(numero=50, abierta=True)
        pedido = Pedido.objects.create(mesa=mesa, estado='cerrado')
        plato_sin_inventario = Plato.objects.create(
            categoria=Categoria.objects.create(nombre='Platos', orden=1),
            nombre='Plato de carta',
            precio=Decimal('30.00'),
        )
        Factura.objects.create(
            pedido=pedido,
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('25'),
            items_snapshot=[{
                'plato': 'Coca-Cola',
                'cantidad': 2,
                'precio_unit': '12.50',
                'subtotal': '25.00',
            }, {
                'plato': plato_sin_inventario.nombre,
                'cantidad': 1,
                'precio_unit': '30.00',
                'subtotal': '30.00',
            }],
        )

        self.client.force_login(self.mesera)
        response = self.client.get(reverse('usuarios:inventario_ventas_turno'))

        self.assertEqual(response.status_code, 200)
        producto = next(row for row in response.json()['productos'] if row['nombre'] == 'Coca-Cola')
        self.assertEqual(producto['saldo_inicial'], '10.00')
        self.assertEqual(producto['entradas'], '5.00')
        self.assertEqual(producto['cantidad_existencia'], '12.00')
        self.assertEqual(producto['cantidad_vendida'], '2')
        self.assertEqual(producto['saldo_final'], '12.00')
        self.assertEqual(producto['precio_venta'], '12.50')
        self.assertEqual(producto['importe_venta'], '25.00')
        self.assertEqual(producto['merma_rotura'], '1.00')
        venta_sin_control = next(row for row in response.json()['productos'] if row['nombre'] == plato_sin_inventario.nombre)
        self.assertFalse(venta_sin_control['control_existencia'])
        self.assertEqual(venta_sin_control['cantidad_vendida'], '1')
        self.assertEqual(venta_sin_control['importe_venta'], '30.00')

    def test_cerrar_turno_crea_resumen_y_notifica_al_admin(self):
        turno = registrar_inicio_turno(self.mesera)
        registrar_inicio_turno(self.cajera)

        mesa = Mesa.objects.create(numero=10, abierta=True)
        pedido = Pedido.objects.create(mesa=mesa, estado='cerrado')
        Factura.objects.create(
            pedido=pedido,
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('250.00'),
            monto_efectivo_cup=Decimal('250.00'),
        )

        turno_cerrado = cerrar_turno(self.cajera)

        self.assertEqual(turno_cerrado.estado, 'cerrado')
        self.assertIn('250.00', turno_cerrado.resumen)
        self.assertTrue(Notificacion.objects.filter(destinatario=self.admin).exists())

    def test_configurar_turno_define_mesas_y_carta_del_turno(self):
        categoria = Categoria.objects.create(nombre='Entradas', orden=1)
        plato = Plato.objects.create(categoria=categoria, nombre='Croquetas', descripcion='Caseras', precio=Decimal('5.00'))

        turno = registrar_inicio_turno(self.admin)
        turno_configurado = configurar_turno(turno, cantidad_mesas=12, platos=[plato])

        self.assertEqual(turno_configurado.cantidad_mesas, 12)
        self.assertEqual(turno_configurado.platos.count(), 1)
        self.assertIn(plato, turno_configurado.platos.all())

    def test_cerrar_turno_incluye_resumen_detallado_de_ventas(self):
        turno = registrar_inicio_turno(self.mesera)
        registrar_inicio_turno(self.cajera)

        mesa = Mesa.objects.create(numero=10, abierta=True)
        pedido = Pedido.objects.create(mesa=mesa, estado='cerrado')
        Factura.objects.create(
            pedido=pedido,
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('200.00'),
            monto_efectivo_cup=Decimal('200.00'),
        )
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='transferencia',
            total_cup=Decimal('300.00'),
            monto_transferencia_cup=Decimal('300.00'),
        )
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='usd',
            total_cup=Decimal('400.00'),
            monto_usd=Decimal('10.00'),
            tasa_cambio=Decimal('40.00'),
        )

        turno_cerrado = cerrar_turno(self.cajera)

        self.assertIn('USD', turno_cerrado.resumen)
        self.assertIn('Efectivo CUP', turno_cerrado.resumen)
        self.assertIn('Transferencia CUP', turno_cerrado.resumen)
        self.assertIn('Total general', turno_cerrado.resumen)
        self.assertIn('900.00', turno_cerrado.resumen)

    def test_mesas_del_turno_limitan_las_mesas_disponibles_para_la_mesera(self):
        for numero in range(1, 15):
            Mesa.objects.get_or_create(numero=numero, defaults={'activa': True})

        turno = registrar_inicio_turno(self.admin)
        configurar_turno(turno, cantidad_mesas=5, platos=[])

        mesas = mesas_del_turno(turno)

        self.assertEqual(list(mesas.values_list('numero', flat=True)), [1, 2, 3, 4, 5])

    def test_mesas_del_turno_respecta_el_rango_1_al_n_configurado(self):
        for numero in range(1, 15):
            Mesa.objects.get_or_create(numero=numero, defaults={'activa': True})

        turno = registrar_inicio_turno(self.admin)
        configurar_turno(turno, cantidad_mesas=5, platos=[])

        mesas = mesas_del_turno(turno)

        self.assertEqual(list(mesas.values_list('numero', flat=True)), [1, 2, 3, 4, 5])

    def test_configurar_turno_crea_las_mesas_faltantes_hasta_la_cantidad(self):
        turno = registrar_inicio_turno(self.admin)
        configurar_turno(turno, cantidad_mesas=5, platos=[])

        mesas = mesas_del_turno(turno)

        self.assertEqual(list(mesas.values_list('numero', flat=True)), [1, 2, 3, 4, 5])
        self.assertEqual(Mesa.objects.filter(activa=True).count(), 5)

    def test_turno_expone_metricas_historicas_y_vista_admin(self):
        turno = registrar_inicio_turno(self.mesera)
        registrar_inicio_turno(self.cajera)

        mesa = Mesa.objects.create(numero=10, abierta=True)
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('200.00'),
            monto_efectivo_cup=Decimal('200.00'),
        )
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='transferencia',
            total_cup=Decimal('300.00'),
            monto_transferencia_cup=Decimal('300.00'),
        )

        turno_cerrado = cerrar_turno(self.cajera)

        self.assertEqual(turno_cerrado.total_general_cup, Decimal('500.00'))
        self.assertEqual(turno_cerrado.total_efectivo_cup, Decimal('200.00'))
        self.assertEqual(turno_cerrado.total_transferencia_cup, Decimal('300.00'))

        request = RequestFactory().get('/usuarios/historial/')
        request.user = self.admin
        response = historial_turnos_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Historial de turnos')
        self.assertContains(response, '500.00')
        self.assertContains(response, 'General')

    def test_admin_puede_ver_detalle_de_turno_especifico_con_ipv(self):
        turno = registrar_inicio_turno(self.mesera)
        registrar_inicio_turno(self.cajera)

        mesa = Mesa.objects.create(numero=11, abierta=True)
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='efectivo_cup',
            total_cup=Decimal('180.00'),
            monto_efectivo_cup=Decimal('180.00'),
        )
        Factura.objects.create(
            pedido=Pedido.objects.create(mesa=mesa, estado='cerrado'),
            mesa_numero=mesa.numero,
            forma_pago='transferencia',
            total_cup=Decimal('220.00'),
            monto_transferencia_cup=Decimal('220.00'),
        )

        turno_cerrado = cerrar_turno(self.cajera)

        request = RequestFactory().get(f'/turnos/{turno_cerrado.pk}/')
        request.user = self.admin
        response = historial_turno_detalle_view(request, turno_cerrado.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Detalle del turno')
        self.assertContains(response, 'IPV')
        self.assertContains(response, '400.00')

    def test_admin_puede_crear_usuario_con_rol_y_password_autenticable(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse('usuarios:crear_usuario'), {
            'username': 'cocina_nueva',
            'first_name': 'Ana',
            'last_name': 'García Pérez',
            'password': 'clave-segura-123',
            'role': 'cocina',
        })

        self.assertRedirects(response, reverse('usuarios:dashboard'))
        usuario = Usuario.objects.get(username='cocina_nueva')
        self.assertEqual(usuario.get_full_name(), 'Ana García Pérez')
        self.assertEqual(usuario.role, 'cocina')
        self.assertTrue(usuario.is_active)
        self.assertTrue(usuario.check_password('clave-segura-123'))
        self.assertTrue(self.client.login(username='cocina_nueva', password='clave-segura-123'))

    def test_login_registra_al_trabajador_con_nombre_completo_en_el_turno(self):
        trabajador = Usuario.objects.create_user(
            username='cocina_turno',
            first_name='Luis',
            last_name='Martínez',
            password='123456',
            role='cocina',
        )

        response = self.client.post(reverse('usuarios:login'), {
            'username': 'cocina_turno',
            'password': '123456',
        })

        self.assertRedirects(response, reverse('pedidos:cocina'))
        turno = get_turno_abierto()
        self.assertIsNotNone(turno)
        self.assertIn(trabajador, turno.usuarios.all())
        self.assertEqual(trabajador.get_full_name(), 'Luis Martínez')

        self.client.force_login(self.admin)
        response = self.client.get(reverse('usuarios:dashboard'))
        self.assertContains(response, 'Luis Martínez')
        self.assertContains(response, 'cocina_turno')

    def test_admin_puede_desactivar_usuario_y_se_bloquea_su_login(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse('usuarios:cambiar_estado_usuario', args=[self.mesera.pk]))

        self.assertRedirects(response, reverse('usuarios:dashboard'))
        self.mesera.refresh_from_db()
        self.assertFalse(self.mesera.is_active)
        self.assertFalse(self.mesera.activo)
        self.assertFalse(self.client.login(username='mesera1', password='123456'))

    def test_admin_puede_editar_datos_rol_y_password_de_un_usuario(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse('usuarios:editar_usuario', args=[self.mesera.pk]), {
            'first_name': 'María',
            'last_name': 'López',
            'username': 'mesera_actualizada',
            'email': 'maria@example.com',
            'telefono': '55512345',
            'role': 'cajera',
            'password': 'nueva-clave-456',
        })

        self.assertRedirects(response, reverse('usuarios:dashboard'))
        self.mesera.refresh_from_db()
        self.assertEqual(self.mesera.get_full_name(), 'María López')
        self.assertEqual(self.mesera.username, 'mesera_actualizada')
        self.assertEqual(self.mesera.role, 'cajera')
        self.assertTrue(self.mesera.check_password('nueva-clave-456'))
        self.assertTrue(self.client.login(username='mesera_actualizada', password='nueva-clave-456'))

    def test_admin_no_puede_quitarse_su_propio_rol(self):
        self.client.force_login(self.admin)

        self.client.post(reverse('usuarios:editar_usuario', args=[self.admin.pk]), {
            'first_name': 'Admin',
            'last_name': 'Principal',
            'username': 'admin',
            'role': 'cocina',
        })

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'administrador')

    def test_usuario_autenticado_no_carga_el_formulario_de_login(self):
        self.client.force_login(self.mesera)

        response = self.client.get(reverse('usuarios:login'))

        self.assertRedirects(response, reverse('pedidos:mesera'))
