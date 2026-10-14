from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from .models import Categoria, Plato
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
    platos_publicados = Plato.objects.filter(
        disponible=True,
        receta__isnull=False,
    ).distinct()
    categorias = Categoria.objects.prefetch_related(
        Prefetch('platos', queryset=platos_publicados),
    ).all()
    categorias_con_platos = [categoria for categoria in categorias if categoria.platos.all()]
    platos_sin_categoria = list(platos_publicados.filter(categoria__isnull=True))
    if platos_sin_categoria:
        categorias_con_platos.append(SimpleNamespace(
            id='sin-categoria',
            nombre='Sin categoría',
            icono='🍽️',
            platos=SimpleNamespace(all=lambda: platos_sin_categoria),
        ))
    mesa = Mesa.objects.filter(numero=mesa_id, activa=True).first()
    return render(request, 'carta/carta.html', {
        'categorias': categorias_con_platos,
        'platos_sin_categoria': platos_sin_categoria,
        'mesa_id': mesa_id,
        'mesa_etiqueta': mesa.etiqueta if mesa else f'Mesa {mesa_id}',
    })
