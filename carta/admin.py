from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from .models import Categoria, Plato
from inventario.models import RecetaItem

@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'orden']

@admin.register(Plato)
class PlatoAdmin(admin.ModelAdmin):
    class PlatoForm(forms.ModelForm):
        def clean_disponible(self):
            disponible = self.cleaned_data['disponible']
            if disponible and self.instance.pk and RecetaItem.objects.filter(
                plato=self.instance,
                insumo__stock_actual__lte=0,
            ).exists():
                raise ValidationError('No se puede activar este plato: hay productos de su receta sin stock.')
            return disponible

        class Meta:
            model = Plato
            fields = '__all__'

    form = PlatoForm
    list_display = ['nombre', 'categoria', 'destino', 'precio', 'disponible']
    list_filter = ['categoria', 'destino', 'disponible']
    list_editable = ['disponible', 'precio']
