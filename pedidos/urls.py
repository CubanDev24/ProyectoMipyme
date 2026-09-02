from django.urls import path
from . import views
app_name = 'pedidos'
urlpatterns = [
    path('mesera/', views.mesera, name='mesera'),
    path('cocina/', views.cocina, name='cocina'),
    path('caja/', views.caja, name='caja'),
    path('caja/estadisticas/pagina/', views.caja_estadisticas_pagina, name='caja_estadisticas_pagina'),
    path('caja/facturas/', views.facturas_historial, name='facturas_historial'),
    path('caja/estadisticas/', views.caja_estadisticas, name='caja_estadisticas'),
    path('caja/factura/<int:pk>/imprimir/', views.factura_imprimir, name='factura_imprimir'),
    path('caja/factura/<int:pk>/imprimir-web/', views.factura_imprimir_web, name='factura_imprimir_web'),
]
