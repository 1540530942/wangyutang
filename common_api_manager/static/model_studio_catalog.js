// Model catalog + strict real-inference validation for the Model Studio page.
// Renders every callable model with its interface paths (current + historical
// aliases), live-probes each model's /health, and can run a REAL inference to
// prove the model works rather than showing a hard-coded badge.

(function () {
  const container = document.getElementById("catalogBody");
  if (!container) return;

  const TYPE_LABEL = { text: "文本", vision: "视觉", asr: "ASR", tts: "TTS" };
  const TYPE_CLS   = { text: "type-text", vision: "type-vision", asr: "type-asr", tts: "type-tts" };

  function badge(text, cls) {
    return `<span class="cat-badge ${cls}">${text}</span>`;
  }

  async function probeHealth(url) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      return r.ok ? "ok" : "err";
    } catch { return "err"; }
  }

  async function runValidate(model) {
    if (!model.validatable) return { skip: true };
    const resp = await fetch("/common/api/model-studio/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: model.id }),
    });
    return await resp.json();
  }

  function renderRow(m, idx, tbody) {
    const primaryPath = m.paths[0] || "";
    const aliasPaths  = m.paths.slice(1);

    const pathsHtml = [primaryPath, ...aliasPaths]
      .map((p, i) => `<code class="${i === 0 ? "cat-path-main" : "cat-path-alias"}">${p}</code>`)
      .join("");

    const tr = document.createElement("tr");
    tr.className = "cat-row";
    tr.dataset.id = m.id;
    tr.innerHTML = `
      <td class="col-expand">
        <span class="cat-row-chevron">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 5l4 4 4-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </span>
      </td>
      <td class="col-name">${m.name}</td>
      <td class="col-type"><span class="type-tag ${TYPE_CLS[m.type] || ""}">${TYPE_LABEL[m.type] || m.type}</span></td>
      <td class="col-provider">${m.provider}</td>
      <td class="col-path">${pathsHtml}</td>
      <td class="col-health"><span class="cat-health-dot" data-health="pending" title="健康检测中"></span></td>
      <td class="col-validate">
        ${m.validatable
          ? `<button class="acc-validate-btn" data-validate>校验</button>`
          : `<span class="acc-no-validate">需实时音频</span>`}
      </td>`;

    const detailTr = document.createElement("tr");
    detailTr.className = "cat-detail-row";
    detailTr.innerHTML = `<td colspan="7">
      <div class="cat-detail-inner">
        <div id="cat-result-${idx}" class="acc-result">
          ${m.validatable
            ? '<span class="acc-result-hint">点击「校验」运行真实推理</span>'
            : '<span class="acc-result-hint">此模型需音频/健康接口校验</span>'}
        </div>
      </div>
    </td>`;

    tbody.appendChild(tr);
    tbody.appendChild(detailTr);

    // Per-row toggle
    tr.addEventListener("click", (e) => {
      if (e.target.closest("[data-validate]")) return;
      const expanded = tr.classList.toggle("expanded");
      detailTr.classList.toggle("visible", expanded);
    });

    // Health probe
    const dot = tr.querySelector(".cat-health-dot");
    probeHealth(m.health).then(status => {
      dot.dataset.health = status;
      dot.title = status === "ok" ? "在线" : "不可达";
    });

    // Validate button
    const vBtn = tr.querySelector("[data-validate]");
    const resultEl = detailTr.querySelector(`#cat-result-${idx}`);
    if (vBtn) {
      vBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        tr.classList.add("expanded");
        detailTr.classList.add("visible");
        vBtn.disabled = true;
        vBtn.textContent = "推理中…";
        resultEl.innerHTML = badge("真实推理中…", "warn");
        try {
          const data = await runValidate(m);
          if (data.skip) {
            resultEl.innerHTML = badge("不支持自动校验", "warn");
          } else if (data.verified) {
            resultEl.innerHTML =
              badge(`✓ 验证通过 ${data.elapsed_ms}ms`, "ok") +
              `<div class="acc-output">${(data.output || "").slice(0, 200)}${data.output && data.output.length > 200 ? "…" : ""}</div>`;
            vBtn.textContent = "已验证✓";
            dot.dataset.health = "ok";
          } else {
            resultEl.innerHTML =
              badge(`返回但未通过判定 ${data.elapsed_ms}ms`, "warn") +
              `<div class="acc-output">${(data.output || data.error || "").slice(0, 200)}</div>`;
            vBtn.textContent = "校验";
            vBtn.disabled = false;
          }
        } catch (err) {
          resultEl.innerHTML = badge(`错误: ${err.message}`, "err");
          vBtn.textContent = "重试";
          vBtn.disabled = false;
        }
      });
    }

    return { tr, detailTr, vBtn, resultEl, dot, model: m };
  }

  async function render() {
    let catalog;
    try {
      catalog = await (await fetch("/common/api/model-studio/catalog", { cache: "no-store" })).json();
    } catch (e) {
      container.innerHTML = `<div style="padding:16px;color:#f87171">目录加载失败: ${e.message}</div>`;
      return;
    }

    container.innerHTML = "";

    const tbl = document.createElement("table");
    tbl.className = "cat-table";
    tbl.innerHTML = `<thead><tr>
      <th class="col-expand"></th>
      <th class="col-name">模型</th>
      <th class="col-type">类型</th>
      <th class="col-provider">提供方</th>
      <th class="col-path">接口路径（含历史别名）</th>
      <th class="col-health">健康</th>
      <th class="col-validate">真实推理校验</th>
    </tr></thead>`;

    const tbody = document.createElement("tbody");
    tbl.appendChild(tbody);
    container.appendChild(tbl);

    const rows = catalog.models.map((m, i) => renderRow(m, i, tbody));

    const allBtn = document.getElementById("validateAll");
    if (allBtn) {
      allBtn.addEventListener("click", async () => {
        allBtn.disabled = true;
        allBtn.textContent = "校验中…";
        for (const r of rows) {
          if (!r.model.validatable || !r.vBtn) continue;
          r.tr.classList.add("expanded");
          r.detailTr.classList.add("visible");
          r.vBtn.disabled = true;
          r.vBtn.textContent = "推理中…";
          r.resultEl.innerHTML = badge("真实推理中…", "warn");
          try {
            const data = await runValidate(r.model);
            if (data.verified) {
              r.resultEl.innerHTML =
                badge(`✓ ${data.elapsed_ms}ms`, "ok") +
                `<div class="acc-output">${(data.output || "").slice(0, 200)}</div>`;
              r.vBtn.textContent = "已验证✓";
              r.dot.dataset.health = "ok";
            } else {
              r.resultEl.innerHTML = badge(`未通过 ${data.elapsed_ms}ms`, "warn");
              r.vBtn.disabled = false;
              r.vBtn.textContent = "校验";
            }
          } catch (err) {
            r.resultEl.innerHTML = badge(`错误: ${err.message}`, "err");
            r.vBtn.disabled = false;
            r.vBtn.textContent = "重试";
          }
        }
        allBtn.disabled = false;
        allBtn.textContent = "全部真实校验";
      });
    }
  }

  render();
})();
