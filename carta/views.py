from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from .models import Categoria
from .qr import generar_qr_mesa
from pedidos.models import Mesa


def index(request):
    mesas = []
    for mesa in Mesa.objects.filter(activa=True):
        mesa.qr_url, mesa.qr_data = generar_qr_mesa(request, mesa)
        mesas.append(mesa)
    return render(request, 'carta/index.html', {'mesas': mesas})


@xframe_options_exempt
def qr_mesa(request, mesa_numero):
    try:
        mesa = Mesa.objects.get(numero=mesa_numero, activa=True)
    except ObjectDoesNotExist as exc:
        raise Http404('La mesa no existe o no está activa.') from exc

    url, qr_data = generar_qr_mesa(request, mesa)
    return render(request, 'carta/qr_mesa.html', {
        'mesa': mesa,
        'qr_data': qr_data,
        'qr_url': url,
    })


def carta_cliente(request, mesa_id):
    categorias = Categoria.objects.prefetch_related('platos').all()
    categorias_con_platos = [c for c in categorias if c.platos.filter(disponible=True).exists()]
    mesa = Mesa.objects.filter(numero=mesa_id, activa=True).first()
    return render(request, 'carta/carta.html', {
        'categorias': categorias_con_platos,
        'mesa_id': mesa_id,
        'mesa_etiqueta': mesa.etiqueta if mesa else f'Mesa {mesa_id}',
    })
