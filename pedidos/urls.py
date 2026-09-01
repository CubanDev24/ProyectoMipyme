from django.urls import path
from . import views
app_name = 'pedidos'
urlpatterns = [
    path('mesera/', views.mesera, name='mesera'),
    path('cocina/', views.cocina, name='cocina'),
    path('caja/', views.caja, name='caja'),
    path('caja/facturas/', views.facturas_historial, name='facturas_historial'),
    path('caja/estadisticas/', views.caja_estadisticas, name='caja_estadisticas'),
    path('caja/factura/<int:pk>/imprimir/', views.factura_imprimir, name='factura_imprimir'),
]
