const moduleGrid = document.querySelector('#moduleGrid');
const refreshButton = document.querySelector('#refreshButton');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    };
    return map[char];
  });
}

function renderModules(modules) {
  moduleGrid.replaceChildren();
  modules.forEach((module) => {
    const card = document.createElement('article');
    card.className = 'module-card';
    const statusClass = `status-${module.status}`;
    const runtimeHealth = module.runtime_health || {};
    const healthStatus = runtimeHealth.status || 'not checked';
    const healthClass = `health-${healthStatus}`;
    const capabilities = (module.capabilities || [])
      .map((capability) => `<span class="chip">${escapeHtml(capability)}</span>`)
      .join('');
    const publicUrl = module.public_url || module.local_url || module.path_prefix || '#';

    card.innerHTML = `
      <p class="eyebrow ${statusClass}">${escapeHtml(module.status)}</p>
      <h3>${escapeHtml(module.name)}</h3>
      <p class="muted">${escapeHtml(module.summary)}</p>
      <div class="chips">${capabilities}</div>
      <p><strong>Public:</strong> <code>${escapeHtml(publicUrl)}</code></p>
      <p><strong>Health:</strong> <code class="${escapeHtml(healthClass)}">${escapeHtml(healthStatus)}</code></p>
      <p><strong>Image:</strong> <code>${escapeHtml(module.image || 'not assigned')}</code></p>
      <a class="module-link" href="${escapeHtml(publicUrl)}">Open module</a>
    `;
    moduleGrid.append(card);
  });
}

async function loadModules() {
  refreshButton.disabled = true;
  try {
    const [modulesResponse, healthResponse] = await Promise.all([
      fetch('/api/modules'),
      fetch('/api/modules/health')
    ]);
    const data = await modulesResponse.json();
    const healthData = healthResponse.ok ? await healthResponse.json() : { modules: [] };
    const healthById = new Map((healthData.modules || []).map((item) => [item.id, item]));
    const modules = (data.modules || []).map((module) => ({
      ...module,
      runtime_health: healthById.get(module.id)
    }));
    renderModules(modules);
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener('click', loadModules);
loadModules();
