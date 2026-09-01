from carta.models import Categoria, Plato
from pedidos.models import Mesa
from inventario.models import Insumo, RecetaItem, TasaCambio

# Mesas
for i in range(1, 11):
    Mesa.objects.get_or_create(numero=i, defaults={'activa': True})
print(f"Mesas: {Mesa.objects.count()}")

# Categorías y platos
Categoria.objects.all().delete()
c1 = Categoria.objects.create(nombre='Entradas', orden=1)
c2 = Categoria.objects.create(nombre='Sopas', orden=2)
c3 = Categoria.objects.create(nombre='Platos Principales', orden=3)
c4 = Categoria.objects.create(nombre='Postres', orden=4)
c5 = Categoria.objects.create(nombre='Bebidas', orden=5)

platos = [
    (c1, 'Croquetas de jamón',    'Caseras, crujientes por fuera',        '3.50'),
    (c1, 'Ensalada mixta',        'Lechuga, tomate, pepino y aceituna',   '4.00'),
    (c1, 'Pan con mantequilla',   'Pan criollo tostado',                  '1.50'),
    (c2, 'Sopa de pollo',         'Con fideos y verduras',                '3.00'),
    (c2, 'Caldo de res',          'Con yuca y plátano',                   '3.50'),
    (c3, 'Ropa vieja',            'Res desmenuzada con sofrito criollo',  '8.50'),
    (c3, 'Pollo asado',           'Con arroz congri y tostones',          '9.00'),
    (c3, 'Filete de cerdo',       'Con puré de malanga y ensalada',       '8.00'),
    (c3, 'Pasta a la boloñesa',   'Con carne molida y salsa napolitana',  '7.50'),
    (c3, 'Arroz con mariscos',    'Con camarones y pulpo',                '11.00'),
    (c4, 'Flan de huevo',         'Casero con caramelo',                  '3.00'),
    (c4, 'Arroz con leche',       'Con canela y limón',                   '2.50'),
    (c4, 'Helado de vainilla',    'Con sirope de chocolate',              '2.00'),
    (c5, 'Refresco',              'Cola, naranja o limonada',             '1.50'),
    (c5, 'Agua natural',          '500 ml',                               '1.00'),
    (c5, 'Jugo natural',          'Mango, guayaba o naranja',             '2.00'),
    (c5, 'Café',                  'Espresso cubano',                      '1.00'),
]
for cat, nombre, desc, precio in platos:
    Plato.objects.create(categoria=cat, nombre=nombre, descripcion=desc, precio=precio)

print(f"Platos: {Plato.objects.count()} en {Categoria.objects.count()} categorías")

# Inventario: insumos de ejemplo
Insumo.objects.all().delete()
insumos = {
    'pollo':      Insumo.objects.create(nombre='Pollo (kg)', unidad='kg', stock_actual=20, stock_minimo=5),
    'res':        Insumo.objects.create(nombre='Carne de res (kg)', unidad='kg', stock_actual=15, stock_minimo=4),
    'cerdo':      Insumo.objects.create(nombre='Filete de cerdo (kg)', unidad='kg', stock_actual=12, stock_minimo=3),
    'pasta':      Insumo.objects.create(nombre='Pasta (kg)', unidad='kg', stock_actual=10, stock_minimo=2),
    'huevo':      Insumo.objects.create(nombre='Huevos', unidad='unidad', stock_actual=60, stock_minimo=12),
    'leche':      Insumo.objects.create(nombre='Leche (l)', unidad='l', stock_actual=8, stock_minimo=2),
    'refresco':   Insumo.objects.create(nombre='Refresco embotellado', unidad='unidad', stock_actual=48, stock_minimo=12),
    'agua':       Insumo.objects.create(nombre='Agua embotellada', unidad='unidad', stock_actual=48, stock_minimo=12),
    'cafe':       Insumo.objects.create(nombre='Café molido (kg)', unidad='kg', stock_actual=3, stock_minimo=1),
}
print(f"Insumos: {Insumo.objects.count()}")

# Recetas de ejemplo: cuánto insumo consume 1 unidad de ciertos platos
recetas = [
    ('Pollo asado', insumos['pollo'], 0.35),
    ('Ropa vieja', insumos['res'], 0.30),
    ('Filete de cerdo', insumos['cerdo'], 0.25),
    ('Pasta a la boloñesa', insumos['pasta'], 0.15),
    ('Pasta a la boloñesa', insumos['res'], 0.10),
    ('Flan de huevo', insumos['huevo'], 2),
    ('Arroz con leche', insumos['leche'], 0.2),
    ('Refresco', insumos['refresco'], 1),
    ('Agua natural', insumos['agua'], 1),
    ('Café', insumos['cafe'], 0.02),
]
for nombre_plato, insumo, cantidad in recetas:
    plato = Plato.objects.filter(nombre=nombre_plato).first()
    if plato:
        RecetaItem.objects.create(plato=plato, insumo=insumo, cantidad=cantidad)
print(f"Ingredientes de receta: {RecetaItem.objects.count()}")

# Tasa de cambio inicial (CUP por 1 USD)
TasaCambio.objects.get_or_create(id=1, defaults={'valor': 380})
print(f"Tasa de cambio actual: 1 USD = {TasaCambio.actual()} CUP")

print("Datos de ejemplo cargados correctamente.")
