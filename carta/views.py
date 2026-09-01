import base64
from io import BytesIO

import qrcode
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from django.urls import reverse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from .models import Categoria
from pedidos.models import Mesa


def _generar_qr_data(request, mesa):
    url = request.build_absolute_uri(reverse('carta:carta', args=[mesa.numero]))
    imagen = qrcode.make(url)
    buffer = BytesIO()
    imagen.save(buffer, format='PNG')
    return url, 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def index(request):
    mesas = []
    for mesa in Mesa.objects.filter(activa=True):
        mesa.qr_url, mesa.qr_data = _generar_qr_data(request, mesa)
        mesas.append(mesa)
    return render(request, 'carta/index.html', {'mesas': mesas})


@xframe_options_exempt
def qr_mesa(request, mesa_numero):
    try:
        mesa = Mesa.objects.get(numero=mesa_numero, activa=True)
    except ObjectDoesNotExist as exc:
        raise Http404('La mesa no existe o no está activa.') from exc

    url, qr_data = _generar_qr_data(request, mesa)
    return render(request, 'carta/qr_mesa.html', {
        'mesa': mesa,
        'qr_data': qr_data,
        'qr_url': url,
    })


def carta_cliente(request, mesa_id):
    categorias = Categoria.objects.prefetch_related('platos').all()
    categorias_con_platos = [c for c in categorias if c.platos.filter(disponible=True).exists()]
    return render(request, 'carta/carta.html', {
        'categorias': categorias_con_platos,
        'mesa_id': mesa_id,
    })
