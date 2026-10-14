from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from carta.models import Categoria, Plato
from inventario.models import (
    CategoriaInventario,
    Insumo,
    MovimientoInventario,
    RecetaItem,
    TasaCambio,
)
from inventario.services import descontar_inventario_por_pedido
from pedidos.models import Factura, ItemPedido, Mesa, Pedido
from usuarios.models import Notificacion, Turno, Usuario


TASA_DEMO = Decimal('380.00')

CATEGORIAS_CARTA = (
    ('Entrantes', (
        (
            'croquetas',
            'Croquetas de jamón',
            'Croquetas doradas, servidas con salsa criolla.',
            '480.00',
            [('jamon', '0.060'), ('harina', '0.040'), ('huevo', '1')],
        ),
        (
            'ensalada',
            'Ensalada fresca',
            'Tomate, pepino y lechuga con aliño de la casa.',
            '520.00',
            [('tomate', '0.100'), ('pepino', '0.080'), ('lechuga', '0.050')],
        ),
        (
            'pan',
            'Pan con ajo',
            'Pan tostado con mantequilla y ajo.',
            '350.00',
            [('pan', '2'), ('mantequilla', '0.020')],
        ),
    )),
    ('Sopas', (
        (
            'sopa_pollo',
            'Sopa criolla de pollo',
            'Caldo de pollo con verduras y fideos.',
            '650.00',
            [('pollo', '0.120'), ('fideos', '0.050'), ('verduras', '0.080')],
        ),
        (
            'caldo_res',
            'Caldo de res',
            'Caldo lento de res con viandas.',
            '720.00',
            [('res', '0.100'), ('viandas', '0.120'), ('verduras', '0.050')],
        ),
    )),
    ('Platos principales', (
        (
            'ropa_vieja',
            'Ropa vieja',
            'Res desmenuzada con sofrito, arroz y frijoles.',
            '1850.00',
            [('res', '0.220'), ('arroz', '0.150'), ('frijoles', '0.100'), ('verduras', '0.080')],
        ),
        (
            'pollo_asado',
            'Pollo asado',
            'Pollo al mojo con arroz y plátano.',
            '1650.00',
            [('pollo', '0.300'), ('arroz', '0.150'), ('platano', '0.120'), ('ajo', '0.010')],
        ),
        (
            'cerdo_plancha',
            'Cerdo a la plancha',
            'Filete de cerdo con viandas y ensalada.',
            '1720.00',
            [('cerdo', '0.250'), ('viandas', '0.150'), ('tomate', '0.060')],
        ),
        (
            'pasta_bolonesa',
            'Pasta a la boloñesa',
            'Pasta con salsa de tomate y carne.',
            '1480.00',
            [('pasta', '0.150'), ('res', '0.100'), ('tomate', '0.120'), ('queso', '0.025')],
        ),
        (
            'pescado',
            'Pescado al limón',
            'Filete a la plancha con arroz y vegetales.',
            '1980.00',
            [('pescado', '0.220'), ('arroz', '0.120'), ('verduras', '0.100')],
        ),
    )),
    ('Postres', (
        (
            'flan',
            'Flan de la casa',
            'Flan de huevo con caramelo.',
            '520.00',
            [('flan_porcion', '1')],
        ),
        (
            'arroz_leche',
            'Arroz con leche',
            'Arroz con leche, canela y toque de limón.',
            '450.00',
            [('arroz_leche_porcion', '1')],
        ),
        (
            'helado',
            'Helado de vainilla',
            'Dos bolas de helado de vainilla con sirope.',
            '620.00',
            [('helado_porcion', '1')],
        ),
        (
            'tarta',
            'Tarta de chocolate',
            'Porción de tarta húmeda de chocolate.',
            '780.00',
            [('tarta_chocolate_porcion', '1')],
        ),
    )),
    ('Bebidas', (
        (
            'refresco',
            'Refresco embotellado',
            'Refresco frío de 350 ml.',
            '380.00',
            [('refresco', '1')],
        ),
        (
            'agua',
            'Agua natural',
            'Agua embotellada de 500 ml.',
            '250.00',
            [('agua', '1')],
        ),
        (
            'jugo',
            'Jugo natural',
            'Jugo de fruta de temporada.',
            '480.00',
            [('fruta', '0.180'), ('azucar', '0.015')],
        ),
        (
            'cafe',
            'Café cubano',
            'Café espresso de tueste intenso.',
            '300.00',
            [('cafe', '0.012')],
        ),
        (
            'limonada',
            'Limonada de la casa',
            'Limonada fresca preparada al momento.',
            '420.00',
            [('limon', '0.100'), ('azucar', '0.020')],
        ),
        (
            'red_bull',
            'Red Bull',
            'Lata energética servida fría.',
            '680.00',
            [('red_bull', '1')],
        ),
        (
            'cerveza',
            'Cerveza nacional',
            'Botella individual servida fría.',
            '620.00',
            [('cerveza', '1')],
        ),
        (
            'ron_botella',
            'Botella de ron añejo',
            'Botella de 750 ml para compartir.',
            '9500.00',
            [('ron', '750')],
        ),
        (
            'mojito',
            'Mojito',
            'Ron, limón, hierbabuena y soda.',
            '850.00',
            [('ron', '50'), ('soda', '1'), ('limon', '0.030'), ('hierbabuena', '0.005'), ('azucar', '0.015')],
        ),
        (
            'cuba_libre',
            'Cuba libre',
            'Ron añejo, refresco de cola y limón.',
            '820.00',
            [('ron', '50'), ('refresco', '1'), ('limon', '0.020')],
        ),
        (
            'daiquiri',
            'Daiquirí',
            'Ron, limón y azúcar, batidos con hielo.',
            '800.00',
            [('ron', '50'), ('limon', '0.030'), ('azucar', '0.015')],
        ),
        (
            'vodka_energetico',
            'Vodka energético',
            'Vodka servido con bebida energética.',
            '980.00',
            [('vodka', '50'), ('red_bull', '1')],
        ),
        (
            'pina_colada',
            'Piña colada',
            'Ron, piña y leche de coco.',
            '920.00',
            [('ron', '50'), ('pina', '0.120'), ('leche_coco', '0.080')],
        ),
    )),
)

INSUMOS_DEMO = (
    ('Pollo', 'cocina', 'Proteínas', 'kg', '40', '5', '0', '320.00'),
    ('Carne de res', 'cocina', 'Proteínas', 'kg', '35', '5', '0', '850.00'),
    ('Cerdo', 'cocina', 'Proteínas', 'kg', '30', '4', '0', '620.00'),
    ('Pescado', 'cocina', 'Proteínas', 'kg', '18', '3', '0', '720.00'),
    ('Jamón', 'cocina', 'Proteínas', 'kg', '8', '2', '0', '680.00'),
    ('Pan', 'cocina', 'Despensa', 'unidad', '120', '20', '0', '60.00'),
    ('Hierbabuena', 'cocina', 'Frutas y vegetales', 'kg', '2', '0.5', '0', '350.00'),
    ('Piña', 'cocina', 'Frutas y vegetales', 'kg', '12', '2', '0', '180.00'),
    ('Leche de coco', 'cocina', 'Lácteos y huevos', 'l', '8', '1', '0', '330.00'),
    ('Harina', 'cocina', 'Despensa', 'kg', '20', '4', '0', '120.00'),
    ('Fideos', 'cocina', 'Despensa', 'kg', '12', '3', '0', '180.00'),
    ('Pasta', 'cocina', 'Despensa', 'kg', '18', '4', '0', '260.00'),
    ('Arroz', 'cocina', 'Despensa', 'kg', '30', '6', '0', '180.00'),
    ('Frijoles', 'cocina', 'Despensa', 'kg', '18', '4', '0', '240.00'),
    ('Huevos', 'cocina', 'Lácteos y huevos', 'unidad', '120', '24', '0', '75.00'),
    ('Leche', 'cocina', 'Lácteos y huevos', 'l', '20', '4', '0', '210.00'),
    ('Queso', 'cocina', 'Lácteos y huevos', 'kg', '8', '2', '0', '950.00'),
    ('Mantequilla', 'cocina', 'Lácteos y huevos', 'kg', '5', '1', '0', '820.00'),
    ('Tomate', 'cocina', 'Frutas y vegetales', 'kg', '18', '4', '0', '160.00'),
    ('Pepino', 'cocina', 'Frutas y vegetales', 'kg', '12', '3', '0', '110.00'),
    ('Lechuga', 'cocina', 'Frutas y vegetales', 'kg', '8', '2', '0', '190.00'),
    ('Verduras', 'cocina', 'Frutas y vegetales', 'kg', '16', '4', '0', '150.00'),
    ('Viandas', 'cocina', 'Frutas y vegetales', 'kg', '24', '5', '0', '140.00'),
    ('Plátano', 'cocina', 'Frutas y vegetales', 'kg', '18', '4', '0', '125.00'),
    ('Ajo', 'cocina', 'Frutas y vegetales', 'kg', '3', '0.5', '0', '420.00'),
    ('Azúcar', 'cocina', 'Despensa', 'kg', '15', '3', '0', '95.00'),
    ('Fruta', 'cocina', 'Frutas y vegetales', 'kg', '14', '3', '0', '240.00'),
    ('Café molido', 'cocina', 'Despensa', 'kg', '5', '1', '0', '1100.00'),
    ('Limón', 'cocina', 'Frutas y vegetales', 'kg', '8', '2', '0', '170.00'),
    ('Refresco embotellado', 'restaurante', 'Bebidas frías', 'unidad', '96', '20', '0', '190.00'),
    ('Agua embotellada', 'restaurante', 'Bebidas frías', 'unidad', '96', '20', '0', '110.00'),
    ('Red Bull', 'restaurante', 'Bebidas frías', 'unidad', '48', '12', '0', '380.00'),
    ('Cerveza nacional', 'restaurante', 'Bebidas frías', 'unidad', '72', '18', '0', '320.00'),
    ('Soda', 'restaurante', 'Bebidas frías', 'unidad', '48', '12', '0', '100.00'),
    ('Ron añejo', 'restaurante', 'Licores', 'ml', '15000', '3000', '0', '4.50'),
    ('Vodka', 'restaurante', 'Licores', 'ml', '7500', '1500', '0', '4.00'),
    ('Flan porción', 'restaurante', 'Postres', 'unidad', '36', '8', '0', '220.00'),
    ('Arroz con leche porción', 'restaurante', 'Postres', 'unidad', '36', '8', '0', '180.00'),
    ('Helado porción', 'restaurante', 'Postres', 'unidad', '48', '10', '0', '260.00'),
    ('Tarta de chocolate porción', 'restaurante', 'Postres', 'unidad', '30', '6', '0', '340.00'),
)

PEDIDOS_POR_DIA = (
    ('ropa_vieja', 2, 'refresco', 1, 'red_bull', 1),
    ('pollo_asado', 1, 'flan', 1, 'cafe', 2, 'helado', 1),
    ('pasta_bolonesa', 2, 'limonada', 1, 'mojito', 1),
    ('pescado', 1, 'ensalada', 1, 'agua', 2, 'cuba_libre', 1),
    ('cerdo_plancha', 2, 'arroz_leche', 1, 'daiquiri', 1),
    ('sopa_pollo', 1, 'croquetas', 1, 'jugo', 1, 'pina_colada', 1),
)


def _momento(fecha, hora, minuto=0):
    return timezone.make_aware(datetime.combine(fecha, time(hora, minuto)))


def _nombre_usuario(usuario, nombre_respaldo):
    if usuario is None:
        return nombre_respaldo
    return usuario.get_full_name() or usuario.username


class Command(BaseCommand):
    help = (
        'Reemplaza carta, inventario y datos operativos con una demostración '
        'de tres días históricos y un turno actual.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirma el borrado de datos operativos y de demostración existentes.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra el alcance de la operación sin modificar la base de datos.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            self._mostrar_alcance()
            return
        if not options['confirm']:
            raise CommandError(
                'Este comando reemplaza los datos operativos. '
                'Revisa primero --dry-run y vuelve a ejecutarlo con --confirm.'
            )

        with transaction.atomic():
            usuarios = list(Usuario.objects.filter(is_active=True, activo=True).order_by('id'))
            tasa = TasaCambio.objects.order_by('-actualizada_en').first()
            if tasa is None:
                tasa = TasaCambio.objects.create(valor=TASA_DEMO)

            self._limpiar_datos_operativos()
            carta = self._crear_carta()
            self._crear_inventario(carta)
            self._crear_turnos_y_ventas(carta, tasa, usuarios)

        self.stdout.write(self.style.SUCCESS(
            'Datos de demostración cargados: carta completa, inventario con recetas, '
            'tres días cerrados con facturas y un turno actual abierto.'
        ))

    def _mostrar_alcance(self):
        self.stdout.write('No se modificó la base de datos.')
        self.stdout.write(f'Base de datos: {connection.settings_dict["NAME"]}')
        self.stdout.write('Se reemplazarían: categorías y platos de la carta,')
        self.stdout.write('categorías, existencias, recetas y movimientos de inventario,')
        self.stdout.write('notificaciones, turnos, pedidos y facturas.')
        self.stdout.write('Se conservarían: usuarios, mesas existentes y tasas de cambio.')
        self.stdout.write(f'Facturas actuales: {Factura.objects.count()}')
        self.stdout.write(f'Pedidos actuales: {Pedido.objects.count()}')
        self.stdout.write(f'Turnos actuales: {Turno.objects.count()}')
        self.stdout.write('Use --confirm para ejecutar el reemplazo.')

    @staticmethod
    def _limpiar_datos_operativos():
        Factura.objects.all().delete()
        Notificacion.objects.all().delete()
        Pedido.objects.all().delete()
        Turno.objects.all().delete()
        MovimientoInventario.objects.all().delete()
        RecetaItem.objects.all().delete()
        Plato.objects.all().delete()
        Insumo.objects.all().delete()
        CategoriaInventario.objects.all().delete()
        Categoria.objects.all().delete()
        for mesa in Mesa.objects.all():
            mesa.abierta = False
            mesa.sesion_id = uuid.uuid4()
            mesa.save(update_fields=['abierta', 'sesion_id'])

    @staticmethod
    def _crear_carta():
        categorias = {}
        platos = {}
        for orden_categoria, (nombre_categoria, platos_categoria) in enumerate(
            CATEGORIAS_CARTA,
            start=1,
        ):
            categoria = Categoria.objects.create(
                nombre=nombre_categoria,
                orden=orden_categoria,
            )
            categorias[nombre_categoria] = categoria
            for orden_plato, datos in enumerate(platos_categoria, start=1):
                clave, nombre, descripcion, precio, receta = datos
                platos[clave] = {
                    'objeto': Plato.objects.create(
                        categoria=categoria,
                        nombre=nombre,
                        descripcion=descripcion,
                        precio=Decimal(precio),
                        disponible=True,
                        orden=orden_plato,
                    ),
                    'receta': receta,
                }
        return {'categorias': categorias, 'platos': platos}

    @staticmethod
    def _crear_inventario(carta):
        categorias = {}
        insumos = {}
        for nombre, tipo, nombre_categoria, unidad, stock, minimo, maximo, costo in INSUMOS_DEMO:
            categoria_key = (tipo, nombre_categoria)
            if categoria_key not in categorias:
                categorias[categoria_key] = CategoriaInventario.objects.create(
                    tipo=tipo,
                    nombre=nombre_categoria,
                    orden=len([key for key in categorias if key[0] == tipo]) + 1,
                )
            insumo = Insumo.objects.create(
                nombre=nombre,
                tipo_inventario=tipo,
                categoria_inventario=categorias[categoria_key],
                categoria=(
                    carta['categorias']['Postres']
                    if tipo == 'restaurante' and nombre in {
                        'Flan porción',
                        'Arroz con leche porción',
                        'Helado porción',
                        'Tarta de chocolate porción',
                    }
                    else carta['categorias']['Bebidas'] if tipo == 'restaurante'
                    else None
                ),
                unidad=unidad,
                stock_actual=Decimal(stock),
                stock_minimo=Decimal(minimo),
                stock_maximo=Decimal(maximo),
                costo=Decimal(costo),
                precio=Decimal('0'),
                disponible=True,
                activo=True,
            )
            insumos[nombre.casefold()] = insumo
            MovimientoInventario.objects.create(
                insumo=insumo,
                tipo_inventario=tipo,
                producto_nombre=insumo.nombre,
                categoria_nombre=categorias[categoria_key].nombre,
                tipo='entrada',
                cantidad=insumo.stock_actual,
                stock_anterior=Decimal('0'),
                stock_posterior=insumo.stock_actual,
                unidad=unidad,
                motivo='Existencia inicial de demostración',
            )

        alias = {
            'pollo': 'Pollo',
            'res': 'Carne de res',
            'cerdo': 'Cerdo',
            'pescado': 'Pescado',
            'jamon': 'Jamón',
            'pan': 'Pan',
            'hierbabuena': 'Hierbabuena',
            'pina': 'Piña',
            'leche_coco': 'Leche de coco',
            'harina': 'Harina',
            'fideos': 'Fideos',
            'pasta': 'Pasta',
            'arroz': 'Arroz',
            'frijoles': 'Frijoles',
            'huevo': 'Huevos',
            'leche': 'Leche',
            'queso': 'Queso',
            'mantequilla': 'Mantequilla',
            'tomate': 'Tomate',
            'pepino': 'Pepino',
            'lechuga': 'Lechuga',
            'verduras': 'Verduras',
            'viandas': 'Viandas',
            'platano': 'Plátano',
            'ajo': 'Ajo',
            'azucar': 'Azúcar',
            'fruta': 'Fruta',
            'cafe': 'Café molido',
            'limon': 'Limón',
            'refresco': 'Refresco embotellado',
            'agua': 'Agua embotellada',
            'red_bull': 'Red Bull',
            'cerveza': 'Cerveza nacional',
            'soda': 'Soda',
            'ron': 'Ron añejo',
            'vodka': 'Vodka',
            'flan_porcion': 'Flan porción',
            'arroz_leche_porcion': 'Arroz con leche porción',
            'helado_porcion': 'Helado porción',
            'tarta_chocolate_porcion': 'Tarta de chocolate porción',
        }
        for plato_data in carta['platos'].values():
            plato = plato_data['objeto']
            RecetaItem.objects.bulk_create([
                RecetaItem(
                    plato=plato,
                    insumo=insumos[alias[clave].casefold()],
                    cantidad=Decimal(cantidad),
                )
                for clave, cantidad in plato_data['receta']
            ])
        MovimientoInventario.objects.filter(
            motivo='Existencia inicial de demostración',
        ).update(creado_en=_momento(timezone.localdate() - timedelta(days=3), 8))

    def _crear_turnos_y_ventas(self, carta, tasa, usuarios):
        hoy = timezone.localdate()
        dias_cerrados = [hoy - timedelta(days=dias_atras) for dias_atras in (3, 2, 1)]
        mesas = list(Mesa.objects.filter(activa=True).order_by('zona', 'numero_zona', 'numero'))
        mesas_salon = [mesa for mesa in mesas if mesa.zona == 'salon']
        mesas_exteriores = [mesa for mesa in mesas if mesa.zona == 'exteriores']
        usuario_por_rol = {
            rol: next((usuario for usuario in usuarios if usuario.role == rol), None)
            for rol in ('administrador', 'cocina', 'mesera', 'cajera')
        }
        platos_activos = [datos['objeto'] for datos in carta['platos'].values()]
        for fecha in dias_cerrados:
            turno = Turno.objects.create(
                fecha=fecha,
                estado='cerrado',
                cantidad_mesas=max(len(mesas_salon), 1),
                cantidad_mesas_exteriores=len(mesas_exteriores),
                observaciones='Turno histórico de demostración.',
            )
            turno.usuarios.add(*usuarios)
            turno.platos.set(platos_activos)
            hora_inicio = _momento(fecha, 10)
            hora_cierre = _momento(fecha, 22)
            Turno.objects.filter(pk=turno.pk).update(
                apertura=hora_inicio,
                cierre=hora_cierre,
            )

            facturas_del_dia = []
            for indice_pedido, lista_items in enumerate(PEDIDOS_POR_DIA):
                mesa = mesas[indice_pedido % len(mesas)] if mesas else None
                hora_pedido = _momento(fecha, 11 + indice_pedido, 15)
                factura = self._crear_venta(
                    hora_pedido=hora_pedido,
                    lista_items=lista_items,
                    mesa=mesa,
                    indice=indice_pedido,
                    tasa=tasa.valor,
                    mesera=usuario_por_rol['mesera'],
                    cajera=usuario_por_rol['cajera'],
                    cocinero=usuario_por_rol['cocina'],
                    platos=carta['platos'],
                )
                facturas_del_dia.append(factura)

            total = sum((factura.total_cup for factura in facturas_del_dia), Decimal('0'))
            efectivo = sum(
                (factura.monto_efectivo_cup for factura in facturas_del_dia),
                Decimal('0'),
            )
            transferencia = sum(
                (factura.monto_transferencia_cup for factura in facturas_del_dia),
                Decimal('0'),
            )
            usd = sum((factura.monto_usd for factura in facturas_del_dia), Decimal('0'))
            resumen = (
                f'Fecha: {fecha:%d/%m/%Y}\n'
                f'Ventas: {len(facturas_del_dia)}\n'
                f'USD: {usd:.2f} USD\n'
                f'Efectivo CUP: {efectivo:.2f} CUP\n'
                f'Transferencia CUP: {transferencia:.2f} CUP\n'
                f'Total general: {total:.2f} CUP\n'
                'Participantes: demostración'
            )
            Turno.objects.filter(pk=turno.pk).update(resumen=resumen)

        turno_actual = Turno.objects.create(
            fecha=hoy,
            estado='abierto',
            cantidad_mesas=max(len(mesas_salon), 1),
            cantidad_mesas_exteriores=len(mesas_exteriores),
            observaciones='Turno abierto con pedidos de demostración en curso.',
        )
        turno_actual.usuarios.add(*usuarios)
        turno_actual.platos.set(platos_activos)
        Turno.objects.filter(pk=turno_actual.pk).update(
            apertura=timezone.now() - timedelta(hours=1),
        )
        self._crear_pedidos_en_curso(
            mesas,
            carta,
        )

    def _crear_venta(
        self,
        hora_pedido,
        lista_items,
        mesa,
        indice,
        tasa,
        mesera,
        cajera,
        cocinero,
        platos,
    ):
        modalidad = 'mesa' if mesa else 'para_llevar'
        sesion_id = uuid.uuid4()
        grupo_para_llevar = uuid.uuid4() if not mesa else None
        if mesa:
            mesa.sesion_id = sesion_id
            mesa.abierta = True
            mesa.save(update_fields=['sesion_id', 'abierta'])

        items_por_destino = {'cocina': [], 'barra': []}
        items_snapshot = []
        total = Decimal('0')
        for posicion in range(0, len(lista_items), 2):
            clave = lista_items[posicion]
            cantidad = int(lista_items[posicion + 1])
            plato = platos[clave]['objeto']
            destino = (
                'barra'
                if plato.categoria.nombre.strip().casefold().startswith('bebid')
                else 'cocina'
            )
            items_por_destino[destino].append((plato, cantidad))
            subtotal = plato.precio * cantidad
            total += subtotal
            items_snapshot.append({
                'plato': plato.nombre,
                'cantidad': cantidad,
                'precio_unit': str(plato.precio),
                'subtotal': str(subtotal),
            })

        momento_pago = hora_pedido + timedelta(minutes=35 + indice * 3)
        pedidos = []
        for destino, items in items_por_destino.items():
            if not items:
                continue
            pedido = Pedido.objects.create(
                mesa=mesa,
                modalidad=modalidad,
                grupo_para_llevar=grupo_para_llevar,
                estado='servido',
                destino=destino,
                nota='Pedido histórico de demostración.',
                sesion_id=sesion_id if mesa else None,
            )
            ItemPedido.objects.bulk_create([
                ItemPedido(pedido=pedido, plato=plato, cantidad=cantidad)
                for plato, cantidad in items
            ])
            Pedido.objects.filter(pk=pedido.pk).update(
                creado_en=hora_pedido,
                actualizado_en=hora_pedido,
            )
            descontar_inventario_por_pedido(pedido, usuario=cocinero)
            MovimientoInventario.objects.filter(
                motivo=f'Pedido #{pedido.pk} servido',
            ).update(creado_en=momento_pago)
            pedidos.append(pedido)

        formas_pago = ('efectivo_cup', 'transferencia', 'mixto', 'usd')
        forma_pago = formas_pago[indice % len(formas_pago)]
        efectivo = Decimal('0')
        transferencia = Decimal('0')
        monto_usd = Decimal('0')
        tasa_venta = None
        if forma_pago == 'efectivo_cup':
            efectivo = total
        elif forma_pago == 'transferencia':
            transferencia = total
        elif forma_pago == 'mixto':
            efectivo = (total / 2).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            transferencia = total - efectivo
        else:
            tasa_venta = tasa
            monto_usd = (total / tasa).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        pedido_principal = pedidos[0]
        factura = Factura.objects.create(
            pedido=pedido_principal,
            mesa_numero=mesa.numero if mesa else None,
            forma_pago=forma_pago,
            total_cup=total,
            monto_efectivo_cup=efectivo,
            monto_transferencia_cup=transferencia,
            monto_usd=monto_usd,
            tasa_cambio=tasa_venta,
            mesera_nombre=_nombre_usuario(mesera, 'Mesera demostración'),
            cajera_nombre=_nombre_usuario(cajera, 'Caja demostración'),
            items_snapshot=items_snapshot,
        )
        Factura.objects.filter(pk=factura.pk).update(creado_en=momento_pago)
        Pedido.objects.filter(pk__in=[pedido.pk for pedido in pedidos]).update(
            estado='cerrado',
            cerrado_en=momento_pago,
            actualizado_en=momento_pago,
        )
        if mesa:
            Mesa.objects.filter(pk=mesa.pk).update(abierta=False)
        return factura

    @staticmethod
    def _crear_pedidos_en_curso(mesas, carta):
        platos = carta['platos']
        claves = ('ropa_vieja', 'pollo_asado', 'refresco')
        estados = ('pendiente', 'en_preparacion', 'listo')
        for indice, (clave, estado) in enumerate(zip(claves, estados)):
            plato = platos[clave]['objeto']
            mesa = mesas[indice] if indice < len(mesas) else None
            modalidad = 'mesa' if mesa else 'para_llevar'
            sesion_id = uuid.uuid4()
            if mesa:
                mesa.sesion_id = sesion_id
                mesa.abierta = True
                mesa.save(update_fields=['sesion_id', 'abierta'])
            hora = timezone.now() - timedelta(minutes=25 - indice * 6)
            pedido = Pedido.objects.create(
                mesa=mesa,
                modalidad=modalidad,
                estado=estado,
                destino=(
                    'barra'
                    if plato.categoria.nombre.strip().casefold().startswith('bebid')
                    else 'cocina'
                ),
                nota='Pedido actual de demostración.',
                sesion_id=sesion_id if mesa else None,
            )
            ItemPedido.objects.create(pedido=pedido, plato=plato, cantidad=1 + (indice % 2))
            Pedido.objects.filter(pk=pedido.pk).update(
                creado_en=hora,
                actualizado_en=hora,
            )
