
// ── Toast notifications ───────────────────────────────────────────────────────

(() => {
  const ICONS  = { success: 'fa-circle-check', info: 'fa-circle-info', warning: 'fa-triangle-exclamation', error: 'fa-circle-xmark' };
  const COLORS = { success: 'text-success-icon', info: 'text-info-icon', warning: 'text-warning-icon', error: 'text-error-icon' };

  window.showToast = function showToast(message, type = 'info') {
    const region = document.getElementById('toast-region');
    if (!region) return;

    const toast = document.createElement('div');
    toast.className = [
      'pointer-events-auto flex items-center gap-3',
      'bg-surface-default border border-default rounded-lg shadow-lg',
      'pl-4 pr-2 py-2.5 w-full lg:w-max',
      'opacity-0 translate-y-2 transition-all duration-300 ease-out',
    ].join(' ');

    toast.innerHTML = `
      <i class="fa-light ${ICONS[type] ?? ICONS.info} ${COLORS[type] ?? COLORS.info} text-base shrink-0" aria-hidden="true"></i>
      <span class="flex-1 text-sm font-medium text-primary lg:whitespace-nowrap">${message}</span>
      <button type="button" aria-label="Dismiss"
        class="shrink-0 ml-1 w-7 h-7 flex items-center justify-center rounded-md text-tertiary hover:text-primary hover:bg-interactive-subtle-hover active:bg-interactive-subtle-active cursor-pointer focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-accent">
        <i class="fa-light fa-xmark" aria-hidden="true"></i>
      </button>
    `;

    region.appendChild(toast);

    requestAnimationFrame(() => requestAnimationFrame(() => {
      toast.classList.replace('opacity-0', 'opacity-100');
      toast.classList.replace('translate-y-2', 'translate-y-0');
    }));

    function dismiss() {
      toast.classList.replace('opacity-100', 'opacity-0');
      toast.classList.replace('translate-y-0', 'translate-y-2');
      toast.addEventListener('transitionend', () => toast.remove(), { once: true });
    }

    toast.querySelector('button').addEventListener('click', dismiss);
  };

  // Default toast on page load
  window.showToast('Validation pending', 'success');
})();


// ── Sticky page header border on scroll ──────────────────────────────────────

const pageHeader = document.getElementById('page-header');
document.getElementById('main-content')?.addEventListener('scroll', () => {
  pageHeader?.classList.toggle('border-b', document.getElementById('main-content').scrollTop > 0);
  pageHeader?.classList.toggle('border-subtle', document.getElementById('main-content').scrollTop > 0);
}, { passive: true });


// ── Nav view switching ────────────────────────────────────────────────────────

document.querySelectorAll('[data-nav-link]').forEach(link => {
  link.addEventListener('click', e => e.preventDefault());
});


// ── Fullscreen toggle ─────────────────────────────────────────────────────────

const fullscreenToggle = document.getElementById('fullscreen-toggle');
const fullscreenIcon = document.getElementById('fullscreen-icon');
fullscreenToggle?.addEventListener('click', () => {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen();
  } else {
    document.exitFullscreen();
  }
});
document.addEventListener('fullscreenchange', () => {
  const isFullscreen = !!document.fullscreenElement;
  if (fullscreenIcon) fullscreenIcon.className = isFullscreen ? 'fa-light fa-compress text-base' : 'fa-light fa-expand text-base';
  fullscreenToggle?.setAttribute('aria-label', isFullscreen ? 'Exit fullscreen' : 'Toggle fullscreen');
});


// ── Agent lookup ──────────────────────────────────────────────────────────────

(() => {
  const input    = document.getElementById('agentLookup');
  const dropdown = document.getElementById('agentDropdown');
  const clearBtn = input.nextElementSibling;
  const allItems = [...dropdown.querySelectorAll('li')];

  const show = () => { dropdown.classList.remove('hidden'); input.setAttribute('aria-expanded', 'true'); };
  const hide = () => { dropdown.classList.add('hidden'); input.setAttribute('aria-expanded', 'false'); };

  const filter = () => {
    const q = input.value.trim().toLowerCase();
    let visible = 0;
    allItems.forEach(li => {
      const text = li.textContent.trim().toLowerCase();
      const match = !q || text.includes(q);
      li.style.display = match ? '' : 'none';
      if (match) visible++;
    });
    visible > 0 ? show() : hide();
  };

  input.addEventListener('focus', () => { if (input.value.trim()) filter(); });
  input.addEventListener('input', filter);

  dropdown.querySelectorAll('li button').forEach(btn => {
    btn.addEventListener('mousedown', e => e.preventDefault());
    btn.addEventListener('click', () => {
      input.value = btn.textContent.trim();
      hide();
    });
  });

  clearBtn.addEventListener('click', () => {
    input.value = '';
    input.focus();
    allItems.forEach(li => li.style.display = '');
    show();
  });

  document.addEventListener('click', e => {
    if (!input.closest('.relative')?.contains(e.target)) hide();
  });
})();


// ── Sidebar toggle ────────────────────────────────────────────────────────────

const sidebar = document.getElementById('app-sidebar');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
const sidebarToggles = [
  document.getElementById('nav-hamburger'),
];

function setSidebar(open) {
  const isOverlay = window.matchMedia('(max-width: 1023px)').matches;
  sidebar.classList.toggle('hidden', !open);
  if (isOverlay) {
    sidebarBackdrop.classList.toggle('hidden', !open);
  } else {
    sidebarBackdrop.classList.add('hidden');
  }
  sidebarToggles.forEach(btn => btn && btn.setAttribute('aria-expanded', String(open)));
}

sidebarToggles.forEach(btn => {
  if (!btn) return;
  btn.addEventListener('click', () => setSidebar(sidebar.classList.contains('hidden')));
});

sidebarBackdrop?.addEventListener('click', () => setSidebar(false));

// Auto-close below lg (1024px)
const mq = window.matchMedia('(max-width: 1023px)');
mq.addEventListener('change', e => { if (e.matches) setSidebar(false); });
setSidebar(!mq.matches);

// Focus trap — only active when sidebar is an overlay (mobile/tablet)
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  if (!window.matchMedia('(max-width: 1023px)').matches) return;
  if (sidebar.classList.contains('hidden')) return;
  const focusable = [...sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

document.querySelectorAll('[data-nav-toggle]').forEach(btn => {
  btn.addEventListener('click', () => {
    const panel = btn.nextElementSibling;
    const icon  = btn.querySelector('i');
    const open  = btn.getAttribute('aria-expanded') === 'true';

    btn.setAttribute('aria-expanded', !open);
    panel.classList.toggle('hidden', open);
    icon.classList.toggle('-rotate-90', open);
  });
});


// ── Target date picker ────────────────────────────────────────────────────────

(function () {
  const toggle   = document.getElementById('targetDateToggle');
  const picker   = document.getElementById('targetDatePicker');
  const backdrop = document.getElementById('datePickerBackdrop');
  const input    = document.getElementById('targetDate');

  const gridButtons = () => [...picker.querySelectorAll('[role="grid"] button:not([disabled])')];

  function open() {
    picker.classList.remove('hidden');
    backdrop.classList.remove('hidden');
    toggle.setAttribute('aria-expanded', 'true');
    const pressed = picker.querySelector('[aria-selected="true"]') || gridButtons()[0];
    if (pressed) pressed.focus();
  }
  function close() {
    picker.classList.add('hidden');
    backdrop.classList.add('hidden');
    toggle.setAttribute('aria-expanded', 'false');
  }

  toggle.addEventListener('click', () => {
    picker.classList.contains('hidden') ? open() : close();
  });

  backdrop.addEventListener('click', close);

  picker.querySelectorAll('[role="grid"] button').forEach(btn => {
    btn.addEventListener('click', () => {
      const day = btn.textContent.trim().padStart(2, '0');
      input.value = `${day}/04/2026`;
      picker.querySelectorAll('[role="grid"] button').forEach(b => {
        b.classList.remove('bg-interactive-default', 'text-interactive-text', 'hover:bg-interactive-hover', 'active:bg-interactive-active', 'font-semibold');
        b.classList.add('text-primary', 'hover:bg-interactive-subtle-hover', 'active:bg-interactive-subtle-active');
        b.removeAttribute('aria-selected');
      });
      btn.classList.add('bg-interactive-default', 'text-interactive-text', 'hover:bg-interactive-hover', 'active:bg-interactive-active', 'font-semibold');
      btn.classList.remove('text-primary', 'text-secondary', 'hover:bg-interactive-subtle-hover');
      btn.setAttribute('aria-selected', 'true');
      close();
      toggle.focus();
    });
  });

  picker.addEventListener('keydown', e => {
    if (picker.classList.contains('hidden')) return;
    const btns = gridButtons();
    const focused = document.activeElement;
    const idx = btns.indexOf(focused);
    if (idx === -1) return;
    const cols = 7;
    let next = -1;
    if (e.key === 'ArrowRight') next = Math.min(idx + 1, btns.length - 1);
    else if (e.key === 'ArrowLeft') next = Math.max(idx - 1, 0);
    else if (e.key === 'ArrowDown') next = Math.min(idx + cols, btns.length - 1);
    else if (e.key === 'ArrowUp') next = Math.max(idx - cols, 0);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = btns.length - 1;
    else return;
    e.preventDefault();
    btns[next].focus();
  });

  const footerBtns = picker.querySelectorAll('.border-t button');
  footerBtns[0].addEventListener('click', close);
  footerBtns[1].addEventListener('click', () => { input.value = ''; close(); });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !picker.classList.contains('hidden')) {
      close();
      toggle.focus();
    }
  });
})();
