from django.contrib import admin
from .models import Categoria, Plato

@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'orden']

@admin.register(Plato)
class PlatoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'categoria', 'precio', 'disponible']
    list_filter = ['categoria', 'disponible']
    list_editable = ['disponible', 'precio']
