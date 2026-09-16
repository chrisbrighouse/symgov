// ── Dialog ────────────────────────────────────────────────────────────────────

const dialogTrigger = document.getElementById('dialogTrigger');
const dialog = document.getElementById('modal');
const overlay = document.getElementById('dialogOverlay');
const dialogClose = document.getElementById('dialogClose');
const dialogCancel = document.getElementById('dialogCancel');
const dialogConfirm = document.getElementById('dialogConfirm');
let lastFocusedElement = null;

function getDialogFocusableElements() {
  if (!dialog) return [];
  return Array.from(dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
    .filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null);
}

function openDialog() {
  if (!dialog || !overlay) return;
  lastFocusedElement = document.activeElement;
  dialog.classList.remove('hidden');
  overlay.classList.remove('hidden');
  dialogTrigger?.setAttribute('aria-expanded', 'true');
  const focusables = getDialogFocusableElements();
  (focusables[0] || dialog).focus();
}

function closeDialog() {
  if (!dialog || !overlay) return;
  dialog.classList.add('hidden');
  overlay.classList.add('hidden');
  dialogTrigger?.setAttribute('aria-expanded', 'false');
  if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
    lastFocusedElement.focus();
  }
}

function handleDialogKeydown(event) {
  if (!dialog || dialog.classList.contains('hidden')) return;

  if (event.key === 'Escape') {
    event.preventDefault();
    closeDialog();
    return;
  }

  if (event.key !== 'Tab') return;

  const focusables = getDialogFocusableElements();
  if (focusables.length === 0) {
    event.preventDefault();
    dialog.focus();
    return;
  }

  const first = focusables[0];
  const last = focusables[focusables.length - 1];

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}


if (dialogTrigger && dialog) {
  dialogTrigger.addEventListener('click', openDialog);
  dialogClose?.addEventListener('click', closeDialog);
  dialogCancel?.addEventListener('click', closeDialog);
  dialogConfirm?.addEventListener('click', closeDialog);
  overlay?.addEventListener('click', closeDialog);
  dialog.addEventListener('keydown', handleDialogKeydown);
}


// ── Toast notifications ───────────────────────────────────────────────────────

(function () {
  const ICONS = {
    success: 'fa-circle-check',
    info:    'fa-circle-info',
    warning: 'fa-triangle-exclamation',
    error:   'fa-circle-xmark',
  };
  const COLORS = {
    success: 'text-success-icon',
    info:    'text-info-icon',
    warning: 'text-warning-icon',
    error:   'text-error-icon',
  };

  window.showToast = function showToast(message, type = 'success') {
    const region = document.getElementById('toast-region');
    if (!region) return;

    const iconClass  = ICONS[type]  ?? ICONS.success;
    const colorClass = COLORS[type] ?? COLORS.success;

    const toast = document.createElement('div');
    toast.className = [
      'pointer-events-auto flex items-center gap-3',
      'bg-surface-default border border-default rounded-lg shadow-lg',
      'pl-4 pr-2 py-3 min-w-64 max-w-sm w-max',
      'opacity-0 translate-y-2 transition-all duration-300 ease-out',
    ].join(' ');

    toast.innerHTML = `
      <i class="fa-light ${iconClass} ${colorClass} text-base shrink-0" aria-hidden="true"></i>
      <span class="flex-1 text-sm font-medium text-primary">${message}</span>
      <button type="button" aria-label="Dismiss"
        class="shrink-0 ml-1 w-7 h-7 flex items-center justify-center rounded-md text-tertiary hover:text-primary hover:bg-interactive-subtle-hover active:bg-interactive-subtle-active transition-colors cursor-pointer focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-accent">
        <i class="fa-light fa-xmark" aria-hidden="true"></i>
      </button>
    `;

    region.appendChild(toast);

    // Animate in on next two frames to ensure transition fires
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
    setTimeout(dismiss, 4000);
  };

  // Wire demo trigger buttons
  document.querySelectorAll('[data-toast]').forEach(btn => {
    btn.addEventListener('click', () => {
      window.showToast(
        btn.dataset.toastMessage || 'Action completed',
        btn.dataset.toast
      );
    });
  });
})();
