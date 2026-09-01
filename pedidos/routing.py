from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/mesera/$', consumers.MeseraConsumer.as_asgi()),
    re_path(r'ws/cocina/$', consumers.CocinaConsumer.as_asgi()),
    re_path(r'ws/caja/$', consumers.CajaConsumer.as_asgi()),
    re_path(r'ws/cliente/(?P<mesa_numero>\d+)/$', consumers.ClienteConsumer.as_asgi()),
]
