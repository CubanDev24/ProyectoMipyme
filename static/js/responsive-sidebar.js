document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('body.bg-slate-900 > aside').forEach((sidebar, index) => {
    const nav = sidebar.querySelector('nav');
    if (!nav) return;

    const activeItem = [...nav.children].find(item =>
      item.matches('[aria-current="page"]') ||
      item.classList.contains('activo') ||
      item.className.toString().includes('bg-emerald-500/10')
    ) || nav.querySelector('a, button');
    const activeLabel = activeItem?.textContent.replace(/\s+/g, ' ').trim();
    const navId = nav.id || `responsive-sidebar-nav-${index + 1}`;
    nav.id = navId;
    nav.classList.add('responsive-sidebar-nav');

    const logoutLink = sidebar.querySelector('a[href*="logout"]');
    if (logoutLink) {
      logoutLink.classList.add('responsive-sidebar-original-logout');
      const responsiveLogoutLink = logoutLink.cloneNode(true);
      responsiveLogoutLink.classList.add('responsive-sidebar-logout');
      nav.appendChild(responsiveLogoutLink);
    }

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'responsive-sidebar-trigger';
    trigger.setAttribute('aria-controls', navId);
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', 'Mostrar opciones de navegación');
    trigger.innerHTML = `<span class="responsive-sidebar-trigger-label">Menú${activeLabel ? ` / ${activeLabel}` : ''}</span><span class="responsive-sidebar-trigger-chevron" aria-hidden="true">⌄</span>`;
    nav.insertAdjacentElement('beforebegin', trigger);

    const closeMenu = () => {
      trigger.setAttribute('aria-expanded', 'false');
      trigger.setAttribute('aria-label', 'Mostrar opciones de navegación');
      nav.classList.remove('is-open');
    };

    trigger.addEventListener('click', () => {
      const expanded = trigger.getAttribute('aria-expanded') !== 'true';
      trigger.setAttribute('aria-expanded', String(expanded));
      trigger.setAttribute('aria-label', `${expanded ? 'Ocultar' : 'Mostrar'} opciones de navegación`);
      nav.classList.toggle('is-open', expanded);
    });

    nav.addEventListener('click', event => {
      if (event.target.closest('a')) closeMenu();
    });
    sidebar.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeMenu();
    });
  });
});