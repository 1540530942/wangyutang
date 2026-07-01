// Model catalog + strict real-inference validation for the lab page.
// Renders every callable model with its interface paths (current + historical
// aliases), live-probes each model's /health, and can run a REAL inference to
// prove the model works rather than showing a hard-coded badge.

(function () {
  const body = document.getElementById("catalogBody");
  if (!body) return;

  function badge(text, cls) {
    return `<span class="cat-badge ${cls}">${text}</span>`;
  }

  async function probeHealth(model, cell) {
    cell.innerHTML = badge("探测中", "warn");
    try {
      const resp = await fetch(model.health, { cache: "no-store" });
      if (resp.ok) {
        cell.innerHTML = badge("在线", "ok");
      } else {
        cell.innerHTML = badge(`HTTP ${resp.status}`, "err");
      }
    } catch (e) {
      cell.innerHTML = badge("不可达", "err");
    }
  }

  async function validate(model, cell) {
    if (!model.validatable) {
      cell.innerHTML = badge("需音频/健康校验", "warn");
      return;
    }
    cell.innerHTML = badge("真实推理中…", "warn");
    try {
      const resp = await fetch("/common/api/lab/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: model.id }),
      });
      const data = await resp.json();
      if (data.ok && data.verified) {
        cell.innerHTML = badge(`✓ 已验证 ${data.elapsed_ms}ms`, "ok") +
          `<div class="cat-out" title="${(data.output || "").replace(/"/g, "&quot;")}">${(data.output || "").slice(0, 40)}</div>`;
      } else if (data.ok) {
        cell.innerHTML = badge(`有返回但未通过判定 ${data.elapsed_ms}ms`, "warn") +
          `<div class="cat-out">${(data.output || "").slice(0, 40)}</div>`;
      } else {
        cell.innerHTML = badge(`失败: ${data.error || "?"}`, "err");
      }
    } catch (e) {
      cell.innerHTML = badge(`错误: ${e.message}`, "err");
    }
  }

  function pathsHtml(paths) {
    const primary = `<code>${paths[0]}</code>`;
    if (paths.length < 2) return primary;
    const aliases = paths.slice(1).map((p) => `<code class="alias">${p}</code>`).join(" ");
    return `${primary}<div class="alias-row" title="历史/别名路径">别名: ${aliases}</div>`;
  }

  async function render() {
    let catalog;
    try {
      catalog = await (await fetch("/common/api/lab/catalog", { cache: "no-store" })).json();
    } catch (e) {
      body.innerHTML = `<tr><td colspan="6">目录加载失败: ${e.message}</td></tr>`;
      return;
    }
    body.innerHTML = "";
    const rows = [];
    catalog.models.forEach((m) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><strong>${m.name}</strong></td>` +
        `<td><span class="type-tag type-${m.type}">${m.type}</span></td>` +
        `<td>${m.provider}</td>` +
        `<td class="paths">${pathsHtml(m.paths)}</td>` +
        `<td class="health-cell"></td>` +
        `<td class="validate-cell">${m.validatable ? '<button class="cat-validate">校验</button>' : badge("health 校验", "warn")}</td>`;
      body.appendChild(tr);
      const healthCell = tr.querySelector(".health-cell");
      const validateCell = tr.querySelector(".validate-cell");
      probeHealth(m, healthCell);
      const btn = tr.querySelector(".cat-validate");
      if (btn) btn.addEventListener("click", () => validate(m, validateCell));
      rows.push({ model: m, validateCell });
    });

    const allBtn = document.getElementById("validateAll");
    if (allBtn) {
      allBtn.addEventListener("click", async () => {
        allBtn.disabled = true;
        for (const r of rows) {
          if (r.model.validatable) await validate(r.model, r.validateCell);
        }
        allBtn.disabled = false;
      });
    }
  }

  render();
})();
