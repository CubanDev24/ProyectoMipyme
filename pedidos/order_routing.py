def destino_para_plato(plato):
    categoria = plato.categoria.nombre.strip().casefold() if plato.categoria else ''
    if categoria.startswith(('bebid', 'refresc', 'licor')):
        return 'barra'
    return 'cocina'
