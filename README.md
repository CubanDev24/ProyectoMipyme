# Las Cuquis— Sistema de carta y pedidos en tiempo real

## Instalación rápida

```powershell
pip install -r requirements.txt
python manage.py makemigrations carta pedidos inventario
python manage.py migrate
python manage.py cargar_datos_demo --dry-run
python manage.py cargar_datos_demo --confirm
python manage.py runserver
```

El comando de demostración reemplaza la carta, el inventario, los turnos,
pedidos, facturas y movimientos existentes. Conserva los usuarios, las mesas
configuradas y las tasas de cambio; reinicia el estado de las mesas y abre las
que tienen pedidos actuales. Genera tres días históricos cerrados, con seis
facturas por día, y un turno actual abierto. Revisa el alcance con `--dry-run`
antes de confirmar.

La carta de demostración incluye energizantes, cervezas, licores, cocteles y
postres. Sus existencias se gestionan en Restaurante y se descuentan mediante
las recetas de cada producto vendido; los postres preparados se controlan por
porción.

En la página de inicio (`/`) se muestra un código QR por cada mesa activa. Al
escanearlo, el teléfono abre directamente la carta asociada a esa mesa. Para
probarlo desde un teléfono conectado a la misma red Wi-Fi, inicia Django
escuchando en la red y abre la dirección usando la IP del computador:

```powershell
python manage.py runserver 0.0.0.0:8000
```

Por ejemplo: `http://192.168.1.20:8000/`. Desde allí se pueden imprimir los QR
con el botón **Imprimir QR**.

## URLs del sistema (sin autenticación)

| URL | Sección | Quién la usa |
|---|---|---|
| `/` | Inicio | Selección de rol |
| `/pedidos/mesera/` | **Mesera** | Toma pedidos por mesa, ve estados, marca servido |
| `/pedidos/cocina/` | **Cocina** | Ve pedidos en tiempo real, cambia estado y consulta el stock de Cocina (solo lectura) |
| `/pedidos/caja/` | **Caja** | Verifica inventario, cobra y cierra cuentas |
| `/mesa/<N>/` | **Cliente** | Ve carta, pide, solicita cuenta |
| `/administrador/` | **Administrador** | Mesas del turno y sus QR, inventario, recetas y tasa de cambio |
| `/admin/` | Admin Django | Gestión de datos (opcional, mismo modelo) |

## Flujo completo

1. **Administrador** en `/administrador/` da de alta los insumos de inventario (nombre,
   unidad, stock actual y mínimo) y, por cada plato de la carta, indica qué insumos
   consume y en qué cantidad (la "receta"). Cada plato publicado requiere al menos
   un insumo activo de Cocina o Restaurante; el inventario no crea platos de carta.
   También configura la tasa de cambio
   CUP/USD vigente. En el panel de turno puede activar o desactivar cada mesa por
   separado, ver su QR y agregar mesas de salón o exteriores.
2. **Mesera** abre `/pedidos/mesera/`, selecciona mesa, agrega platos y envía a cocina
   - También puede el **cliente** desde `/mesa/<N>/`
   - **Caja o el administrador** pueden cerrar el turno desde el panel de turno.
3. **Cocina** en `/pedidos/cocina/` recibe los platos → los pasa a "En preparación" → "Listo".
   Las bebidas se preparan desde **Barra** en el panel de la mesera, donde se marcan
  como "Listo" al terminar. Cocina también muestra las unidades vendidas hoy por
  plato, junto con los insumos de su receta, la cantidad por plato y el consumo
   Mientras un pedido esté pendiente, la mesera puede solicitar su cancelación e
   indicar el motivo; Cocina o Barra deben aceptarla para que el pedido se retire.
  estimado total para las unidades vendidas, sin mostrar importes, y permite consultar los productos, existencias y
  mínimos del inventario de Cocina en una sección de solo lectura.
   Mientras un pedido esté pendiente, la mesera puede solicitar su cancelación e
   indicar el motivo; Cocina o Barra deben aceptarla para que el pedido se retire.
4. **Mesera** recoge el pedido listo → lo marca como "Servido". El servidor no permite
   servir un pedido que todavía no esté listo.
   En este momento el sistema descuenta automáticamente el inventario según la
   receta de cada plato, y si algún insumo queda en o por debajo de su stock
   mínimo (o no alcanzaba), se envía un aviso en tiempo real a **Caja**.
5. **Cliente** (o mesera) solicita la cuenta de la mesa. La solicitud agrupa todos
  los pedidos activos de esa mesa en una sola cuenta.
6. **Caja** en `/pedidos/caja/` ve la cuenta agrupada, la selecciona, revisa el total y
   elige la forma de pago:
   - **Efectivo CUP** o **Transferencia**: se cobra el total completo en esa modalidad.
   - **Mixto**: la mitad del total en efectivo CUP y la otra mitad por transferencia.
   - **USD**: se indica la tasa de cambio (precargada desde Administrador, editable)
     y el sistema calcula el monto a cobrar en dólares a partir del total en CUP.

  Al confirmar, se registra una factura de la mesa y se cierran todos sus pedidos;
  luego se
   puede imprimir opcionalmente una facturita para el cliente.

## Inventario

- El panel de administración separa las existencias en inventario de **Cocina**
  (ingredientes de recetas) e inventario de **Restaurante** (productos listos para
  vender). Cada área gestiona sus propias categorías; los productos que ya existían
  antes de la separación quedan en "Por clasificar" hasta que el administrador los
  asigne, sin alterar stock, recetas ni movimientos históricos.
- Los productos de Restaurante se vinculan a una categoría de inventario y a una
  categoría de la carta; los insumos de Cocina no se publican automáticamente en la
  carta. Entradas, salidas, mermas, stock mínimo/máximo e historial siguen disponibles
  en ambas áreas.
- App `inventario`: modelos `Insumo` (stock y área), `CategoriaInventario`,
  `RecetaItem` (consumo de insumo por
  plato) y `TasaCambio` (histórico de tasas CUP/USD, se usa la más reciente).
- El descuento de stock ocurre una sola vez por pedido (campo
  `Pedido.inventario_descontado`), disparado cuando la mesera marca el pedido
  como "Servido".
- El IPV se controla por turno con productos seleccionados de la carta, no con
  insumos ni movimientos de ingredientes. Al iniciar, caja registra el fondo
  inicial CUP, saldo inicial por producto y entradas del almacén; las roturas se
  registran durante el turno. Las facturas suman automáticamente las unidades
  vendidas y el saldo final pasa como sugerencia inicial al siguiente turno.
- Los avisos de inventario (`stock bajo` o `insuficiente`) se transmiten por
  WebSocket al grupo `caja` y se muestran como notificaciones + un panel de
  alertas recientes en la pantalla de Caja.
- Los historiales de movimientos, facturas y turnos, así como las listas de productos
  de inventario, se muestran paginados para facilitar la consulta de registros extensos.
- El administrador tiene una sección de **Reportes** con ventas por producto y
  categoría, recaudación y formas de pago. Los informes se pueden consultar por
  semana actual, mes actual, mes anterior, año actual o un rango de fechas
  personalizado; las facturas guardan también la categoría del plato al cobrarse.

## Facturación

- App `pedidos`, modelo `Factura`: guarda forma de pago, montos por modalidad,
  tasa de cambio usada (si aplica) y una copia (`items_snapshot`) de los platos de
  todos los pedidos de la mesa, para que la facturita se pueda reimprimir aunque
  cambien los precios de la carta más adelante.
- Los montos a cobrar siempre se calculan en el servidor a partir del total real
  del pedido (no se confía en lo que envíe el navegador), y no se puede cerrar
  dos veces la misma cuenta.
- En Caja, los pagos en efectivo CUP y USD requieren registrar el importe
  recibido; el sistema valida que cubra la cuenta, calcula el cambio y lo guarda
  en la factura para mostrarlo en el ticket impreso.
- `/pedidos/caja/factura/<id>/imprimir/` abre una facturita lista para imprimir
  (se puede abrir en una pestaña nueva desde el botón "Imprimir facturita").

## WebSockets

| Canal | Grupo |
|---|---|
| `ws/mesera/` | `mesera` |
| `ws/cocina/` | `cocina` |
| `ws/caja/` | `caja` |
| `ws/cliente/<mesa>/` | `cliente_<N>` |

## Para producción

- Sustituir `InMemoryChannelLayer` por Redis:
  ```python
  pip install channels-redis
  CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {'hosts': [('127.0.0.1', 6379)]}}}
  ```
- Cambiar SQLite por PostgreSQL
- Configurar `ALLOWED_HOSTS` y `SECRET_KEY` reales

## hecho
- Correccion del error de csrf token y las sesiones de usuario.
- corregido el inventario del administrador. 
- Corregida la creacion y edicion de usuarios por el administrador.
- Se agrego la opcion para modificar las imagenes del inventario y la carta.
- Se eliminaron los datos de prueba que habia en la vista de la mesera con respecto a los pedidos.
- Se agrego la imagen del pago por transferencia
- Se corrigio el flujo para que el cliente no envie pedido a la cocina sino a la mesera.
- Se corrigio para que salgan bien los precios de todos los platos al cliente y la mesera.
- Se corrigio la funcion de llamada a la mesera 
- que salga el historial depedidos.
- Cuando el plato esta listo ya notifica a la mesera.
- La imagen de cada plato sale en la cocina.
- Se le agrego una frase a la factura.
- Se cambio el flujo y se agrego una seccion de barra a la mesera para que las bebidas pudiera manejarlas desde el mismo panel y no salieran en cocina.
- Se agrego la funcionalidad de editar categorias en el panel de administracion.
- Se agregaron las mesas ecteriores.
- Se corrigio para que las modales en la administracion no salgan por encima de otras.
- Se corrigio para que no pueda cobrar antes de que el pedido este servido.
- Se separo el inventario en Cocina y Restaurante, con categorías independientes y
  clasificación manual segura para los productos anteriores.
- La carta se genera a partir de los productos que decida el administrador del inventario.
- Ya se eliminan y editan las categorias.
- Se agrego una reestriccion para que no se pueda cerrar un turno con pedidos o tickets pendientes por cobrar.
- Se implemento el IPV por productos de la carta y turno, con fondo inicial, entradas, roturas registradas durante el turno y ventas automáticas desde los cobros.
- Se restringio el historial de turnos cerrados al administrador, se muestran las horas de apertura y cierre, y se agrego el aviso para continuar o cerrar un turno que pasa de medianoche; el cierre de la cajera notifica al administrador.
- En las ventas del día de Cocina se muestran por plato las unidades vendidas, su imagen, los insumos de la receta, la cantidad por unidad y el consumo total estimado, sin importes.
- La mesera puede solicitar cancelar pedidos pendientes indicando el motivo; la estación de destino acepta la solicitud y el pedido se retira de sus listados.
- El motivo de cancelación se escribe en un formulario dentro del pedido, sin avisos emergentes del navegador; las solicitudes a Cocina se muestran en una alerta centrada con sonido y fuera de la cola normal.
- Cuando se solicita cancelar un pedido de Barra, la mesera recibe una notificación con el motivo; desde su panel también puede aceptar la solicitud.
- La modal de detalles de una mesa también se puede cerrar haciendo clic fuera del panel.



## Mejoras:
- pedir agregos a un plato o un trago.
- Al agregar un pedido para llevar si tiene algun producto de cocina, agregar un termopad a la compra.
- mejorar las tablas de administracion con funcionalidades de excel.
- crear nuevos roles con permisos definidos para el admin
- cancelar pedidos de cocina en comanda cuando no estan en preparacion o listos
- crear nuevos roles con permisos definidos para el admin.
