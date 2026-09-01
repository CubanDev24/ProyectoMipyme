# Script para cargar datos de ejemplo
# Ejecutar con: python manage.py shell < pedidos/fixtures_data.py
from carta.models import Categoria, Plato
Categoria.objects.all().delete()
c1 = Categoria.objects.create(nombre='Entradas', orden=1)
c2 = Categoria.objects.create(nombre='Platos Principales', orden=2)
c3 = Categoria.objects.create(nombre='Postres', orden=3)
c4 = Categoria.objects.create(nombre='Bebidas', orden=4)
platos = [
    (c1, 'Croquetas de jamón', 'Caseras, crujientes por fuera', '3.50'),
    (c1, 'Ensalada mixta', 'Lechuga, tomate, pepino y aceituna', '4.00'),
    (c1, 'Sopa del día', 'Pregunta al camarero', '3.00'),
    (c2, 'Ropa vieja', 'Res desmenuzada con sofrito criollo', '8.50'),
    (c2, 'Pollo asado', 'Con arroz congri y tostones', '9.00'),
    (c2, 'Filete de cerdo', 'Con puré de malanga y ensalada', '8.00'),
    (c2, 'Pasta a la boloñesa', 'Con carne molida y salsa napolitana', '7.50'),
    (c3, 'Flan de huevo', 'Casero con caramelo', '3.00'),
    (c3, 'Arroz con leche', 'Con canela y limón', '2.50'),
    (c4, 'Refresco', 'Cola, naranja o limonada', '1.50'),
    (c4, 'Agua natural', '500ml', '1.00'),
    (c4, 'Jugo natural', 'Mango, guayaba o naranja', '2.00'),
]
for cat, nombre, desc, precio in platos:
    Plato.objects.create(categoria=cat, nombre=nombre, descripcion=desc, precio=precio)
print(f'Creados {Plato.objects.count()} platos en {Categoria.objects.count()} categorías.')
