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
