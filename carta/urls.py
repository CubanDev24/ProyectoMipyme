from django.urls import path
from . import views
app_name = 'carta'
urlpatterns = [
    path('', views.index, name='index'),
    path('mesa/<int:mesa_numero>/qr/', views.qr_mesa, name='qr_mesa'),
    path('mesa/<int:mesa_id>/', views.carta_cliente, name='carta'),
]
