from decimal import Decimal

from django.test import RequestFactory, TestCase

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
