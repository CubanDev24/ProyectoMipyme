# La Mesa — Sistema de carta y pedidos en tiempo real

## Instalación rápida

```powershell
pip install -r requirements.txt
python manage.py makemigrations carta pedidos inventario
python manage.py migrate
python manage.py shell < datos_ejemplo.py
python manage.py runserver
```

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
| `/pedidos/cocina/` | **Cocina** | Ve pedidos en tiempo real, cambia estado |
| `/pedidos/caja/` | **Caja** | Verifica inventario, cobra y cierra cuentas |
| `/mesa/<N>/` | **Cliente** | Ve carta, pide, solicita cuenta |
| `/administrador/` | **Administrador** | Insumos de inventario, recetas y tasa de cambio |
| `/admin/` | Admin Django | Gestión de datos (opcional, mismo modelo) |

## Flujo completo

1. **Administrador** en `/administrador/` da de alta los insumos de inventario (nombre,
   unidad, stock actual y mínimo) y, por cada plato de la carta, indica qué insumos
   consume y en qué cantidad (la "receta"). También configura la tasa de cambio
   CUP/USD vigente.
2. **Mesera** abre `/pedidos/mesera/`, selecciona mesa, agrega platos y envía a cocina
   - También puede el **cliente** desde `/mesa/<N>/`
3. **Cocina** en `/pedidos/cocina/` recibe el pedido → lo pasa a "En preparación" → "Listo"
4. **Mesera** ve que el plato está listo y lo recoge → lo marca como "Servido".
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

- App `inventario`: modelos `Insumo` (stock), `RecetaItem` (consumo de insumo por
  plato) y `TasaCambio` (histórico de tasas CUP/USD, se usa la más reciente).
- El descuento de stock ocurre una sola vez por pedido (campo
  `Pedido.inventario_descontado`), disparado cuando la mesera marca el pedido
  como "Servido".
- Los avisos de inventario (`stock bajo` o `insuficiente`) se transmiten por
  WebSocket al grupo `caja` y se muestran como notificaciones + un panel de
  alertas recientes en la pantalla de Caja.

## Facturación

- App `pedidos`, modelo `Factura`: guarda forma de pago, montos por modalidad,
  tasa de cambio usada (si aplica) y una copia (`items_snapshot`) de los platos de
  todos los pedidos de la mesa, para que la facturita se pueda reimprimir aunque
  cambien los precios de la carta más adelante.
- Los montos a cobrar siempre se calculan en el servidor a partir del total real
  del pedido (no se confía en lo que envíe el navegador), y no se puede cerrar
  dos veces la misma cuenta.
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



por hacer:
cambiar el estilo visual completo, mejorar la funcionalidad de la administracion para el inventario, al final del dia mostrar el ipv y el cierre y estadisticas de las ventas y finalmente hacer roles para cada turno