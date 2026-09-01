from django.urls import path
from . import views

app_name = 'inventario'
urlpatterns = [
    path('', views.administrador, name='administrador'),
    path('categoria/nueva/', views.categoria_crear, name='categoria_crear'),
    path('plato/nuevo/', views.plato_crear, name='plato_crear'),
    path('plato/<int:pk>/editar/', views.plato_editar, name='plato_editar'),
    path('plato/<int:pk>/eliminar/', views.plato_eliminar, name='plato_eliminar'),
    path('insumo/nuevo/', views.insumo_crear, name='insumo_crear'),
    path('insumo/<int:pk>/editar/', views.insumo_editar, name='insumo_editar'),
    path('insumo/<int:pk>/eliminar/', views.insumo_eliminar, name='insumo_eliminar'),
    path('receta/<int:plato_id>/agregar/', views.receta_agregar, name='receta_agregar'),
    path('receta-item/<int:pk>/eliminar/', views.receta_item_eliminar, name='receta_item_eliminar'),
    path('tasa/', views.tasa_actualizar, name='tasa_actualizar'),
]
