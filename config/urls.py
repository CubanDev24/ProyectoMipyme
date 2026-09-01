from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('usuarios.urls')),
    path('carta/', include('carta.urls')),
    path('pedidos/', include('pedidos.urls')),
    path('administrador/', include('inventario.urls')),
]
