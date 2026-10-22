from django.db import models

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    orden = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['orden', 'nombre']
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
    def __str__(self): return self.nombre

class Plato(models.Model):
    DESTINO_CHOICES = [
        ('cocina', 'Cocina'),
        ('barra', 'Barra'),
    ]

    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='platos',
    )
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    imagen = models.ImageField(upload_to='carta/', blank=True, null=True)
    precio = models.DecimalField(max_digits=8, decimal_places=2)
    destino = models.CharField(max_length=10, choices=DESTINO_CHOICES, default='cocina')
    disponible = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['orden', 'nombre']
    def __str__(self): return self.nombre
