function toast(msg, tipo) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const el = document.createElement('div');
  el.className = `toast${tipo === 'green' ? ' toast-green' : tipo === 'red' ? ' toast-red' : tipo === 'orange' ? ' toast-orange' : tipo === 'blue' ? ' toast-blue' : ''}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function abrirModal(modalId) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal.hidden = false;
  modal.classList.remove('hidden');
  modal.removeAttribute('hidden');
  modal.setAttribute('aria-hidden', 'false');
  modal.style.display = 'block';
}

function cerrarModal(modalId) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  modal.hidden = true;
  modal.classList.add('hidden');
  modal.setAttribute('hidden', 'hidden');
  modal.setAttribute('aria-hidden', 'true');
  modal.style.display = 'none';
}

function mostrarModalFactura({ title, kicker = 'Solicitud', mesaNumero = null, items = [], total = 0, tipo = 'cliente', onAccept = null }) {
  const modal = document.getElementById('modal-factura');
  const titleEl = document.getElementById('modal-factura-title');
  const kickerEl = document.getElementById('modal-factura-kicker');
  const bodyEl = document.getElementById('modal-factura-body');
  const actionBtn = document.getElementById('modal-factura-accept');
  if (!modal || !titleEl || !bodyEl || !actionBtn) return;

  const itemsHtml = (items && items.length) ? items.map(item => `
    <div class="modal-item-row">
      <span>${item.cantidad ?? 1}x ${item.plato || item.nombre || 'Item'}</span>
      <strong>${Number(item.subtotal ?? (item.precio || 0) * (item.cantidad || 1)).toFixed(2)} CUP</strong>
    </div>
  `).join('') : '<div class="modal-empty">Sin detalle de pedido.</div>';

  kickerEl.textContent = kicker;
  titleEl.textContent = title;
  const mesaHtml = mesaNumero ? `<div class="modal-mesa">Mesa ${mesaNumero}</div>` : '';
  const totalHtml = `<div class="modal-total"><span>Total</span><strong>${Number(total).toFixed(2)} CUP</strong></div>`;
  const detalleExtra = tipo === 'mesera' ? '<div class="modal-note">La mesa solicita la factura para proceder con el cobro.</div>' : '<div class="modal-note">Aquí tienes el resumen completo para revisar antes de pagar.</div>';

  bodyEl.innerHTML = `
    ${mesaHtml}
    <div class="modal-lista">${itemsHtml}</div>
    ${totalHtml}
    ${detalleExtra}
  `;

  const aceptar = () => {
    if (typeof onAccept === 'function') onAccept();
    cerrarModal('modal-factura');
  };

  actionBtn.onclick = aceptar;
  abrirModal('modal-factura');
}

function cerrarModalFactura() {
  cerrarModal('modal-factura');
}

let contextoAudio = null;
const VOLUMEN_NOTIFICACION = 0.75;

function esMovilMesera() {
  return document.body.classList.contains('mesera-app')
    && window.matchMedia('(max-width: 767px)').matches;
}

function frecuenciaNotificacion(freq) {
  return esMovilMesera() ? Math.min(1600, Math.max(950, freq * 0.8 + 650)) : freq;
}

function gananciaNotificacion() {
  return esMovilMesera() ? 1 : VOLUMEN_NOTIFICACION;
}

function prepararAudio() {
  try {
    if (!contextoAudio) {
      contextoAudio = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (contextoAudio.state === 'suspended') return contextoAudio.resume();
  } catch(e) {}
  return Promise.resolve();
}

document.addEventListener('pointerdown', prepararAudio, {once: true});

function reproducirTonoNotificacion(ctx, freq, duracion) {
  const movil = esMovilMesera();
  const cantidadPulsos = movil ? 3 : 1;
  const intervaloPulso = movil ? 0.18 : duracion;
  const frecuencia = frecuenciaNotificacion(freq);

  for (let pulso = 0; pulso < cantidadPulsos; pulso += 1) {
    const inicio = ctx.currentTime + pulso * intervaloPulso;
    const fin = inicio + duracion;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = movil ? 'square' : 'sine';
    osc.frequency.value = frecuencia;
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.setValueAtTime(gananciaNotificacion(), inicio);
    gain.gain.exponentialRampToValueAtTime(0.001, fin);
    osc.start(inicio);
    osc.stop(fin);
  }
}

function sonidoAlerta(freq = 880) {
  prepararAudio().then(() => {
    try {
    const ctx = contextoAudio;
    if (!ctx) return;
    const movil = esMovilMesera();
    reproducirTonoNotificacion(ctx, freq, movil ? 0.14 : 0.5);
    } catch(e) {}
  });
}

function activarSonido() {
  try {
    if (!contextoAudio) {
      contextoAudio = new (window.AudioContext || window.webkitAudioContext)();
    }
    const ctx = contextoAudio;
    const reproducir = () => {
      reproducirTonoNotificacion(ctx, 880, esMovilMesera() ? 0.14 : 0.45);
    };
    reproducir();
    if (ctx.state === 'suspended') ctx.resume();
  } catch(e) {}
  document.querySelectorAll('[data-activar-sonido]').forEach(b => {
    b.textContent = 'Sonido activo';
    b.classList.add('sonido-activo');
    b.disabled = true;
  });
}

function sonidoPedidoNuevo() {
  sonidoAlerta(740);
  setTimeout(() => sonidoAlerta(980), 130);
}

function sonidoCambioEstado() {
  sonidoAlerta(560);
}

function sonidoExito() {
  sonidoAlerta(660);
  setTimeout(() => sonidoAlerta(880), 120);
}

function sonidoError() {
  sonidoAlerta(220);
}

function sonidoCuenta() {
  sonidoAlerta(620);
  setTimeout(() => sonidoAlerta(760), 150);
}

function sonidoMesa() {
  sonidoAlerta(480);
}
