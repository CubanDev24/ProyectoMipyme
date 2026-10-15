import base64
from io import BytesIO

import qrcode
from django.urls import reverse


def generar_qr_mesa(request, mesa):
    url = request.build_absolute_uri(reverse('carta:carta', args=[mesa.numero]))
    imagen = qrcode.make(url)
    buffer = BytesIO()
    imagen.save(buffer, format='PNG')
    return url, 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def _escapar_valor_wifi(valor):
    return valor.replace('\\', '\\\\').replace(';', r'\;').replace(',', r'\,').replace(':', r'\:')


def generar_qr_wifi(ssid, password):
    tipo = 'WPA' if password else 'nopass'
    contenido = f'WIFI:T:{tipo};S:{_escapar_valor_wifi(ssid)};'
    if password:
        contenido += f'P:{_escapar_valor_wifi(password)};'
    imagen = qrcode.make(contenido + ';')
    buffer = BytesIO()
    imagen.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
