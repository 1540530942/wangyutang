const API_BASE = "api/audio/convert";

const elements = {
  form: document.getElementById("convertForm"),
  file: document.getElementById("fileInput"),
  fileHint: document.getElementById("fileHint"),
  format: document.getElementById("formatSelect"),
  formatHint: document.getElementById("formatHint"),
  split: document.getElementById("splitInput"),
  submit: document.getElementById("submitButton"),
  refresh: document.getElementById("refreshButton"),
  status: document.getElementById("statusText"),
  capability: document.getElementById("capabilityPill"),
  resultPanel: document.getElementById("resultPanel"),
  resultSummary: document.getElementById("resultSummary"),
  resultRows: document.getElementById("resultRows"),
  archiveLink: document.getElementById("archiveLink"),
  jobRows: document.getElementById("jobRows"),
  jobsEmpty: document.getElementById("jobsEmpty"),
};

let formatLabels = {};

function humanBytes(value) {
  if (!Number.isFinite(value)) return "-";
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function humanSeconds(value) {
  if (!Number.isFinite(value)) return "-";
  const total = Math.round(value);
  const minutes = Math.floor(total / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function setStatus(message, isError = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", isError);
}

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.detail || JSON.stringify(payload);
  } catch (error) {
    return `HTTP ${response.status}`;
  }
}

async function loadCapabilities() {
  try {
    const response = await fetch(`${API_BASE}/capabilities`);
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();

    elements.format.innerHTML = "";
    data.formats.forEach((format) => {
      formatLabels[format.name] = format.label;
      const option = document.createElement("option");
      option.value = format.name;
      option.textContent = format.name.toUpperCase();
      elements.format.appendChild(option);
    });
    elements.format.value = data.formats.some((f) => f.name === "opus") ? "opus" : data.formats[0].name;
    elements.formatHint.textContent = formatLabels[elements.format.value] || "";

    const maxMb = Math.round(data.max_upload_bytes / 1024 / 1024);
    elements.fileHint.textContent = `支持 ffmpeg 可读的音频格式，单个文件最大 ${maxMb} MB`;

    if (data.available) {
      elements.capability.textContent = `转码可用 · ${data.ffmpeg.replace(/^ffmpeg version /, "ffmpeg ").split(" ").slice(0, 2).join(" ")}`;
      elements.capability.className = "pill ok";
    } else {
      elements.capability.textContent = "转码不可用：服务器未安装 ffmpeg";
      elements.capability.className = "pill bad";
      elements.submit.disabled = true;
    }
  } catch (error) {
    elements.capability.textContent = `能力检查失败：${error.message}`;
    elements.capability.className = "pill bad";
  }
}

function renderResult(manifest) {
  elements.resultRows.innerHTML = "";
  manifest.chunks.forEach((chunk) => {
    const row = document.createElement("tr");
    const cells = [
      String(chunk.index),
      chunk.file,
      humanSeconds(chunk.start),
      humanSeconds(chunk.end),
      humanBytes(chunk.bytes),
    ];
    cells.forEach((text) => {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.appendChild(cell);
    });

    const downloadCell = document.createElement("td");
    const link = document.createElement("a");
    link.href = chunk.path;
    link.download = chunk.file;
    link.textContent = "下载";
    downloadCell.appendChild(link);
    row.appendChild(downloadCell);
    elements.resultRows.appendChild(row);
  });

  const ratio = manifest.source_bytes > 0 ? manifest.output_bytes / manifest.source_bytes : 0;
  const summary = [
    ["任务 ID", manifest.job_id],
    ["源文件", `${manifest.source_name} (${humanBytes(manifest.source_bytes)})`],
    ["时长", humanSeconds(manifest.duration_seconds)],
    ["输出", `${manifest.chunks.length} 个 ${manifest.format.toUpperCase()} · ${humanBytes(manifest.output_bytes)}`],
    ["体积比", `${(ratio * 100).toFixed(1)}%`],
    ["耗时", `${manifest.elapsed_seconds}s`],
  ];
  elements.resultSummary.innerHTML = "";
  summary.forEach(([label, value]) => {
    const block = document.createElement("div");
    const caption = document.createElement("span");
    caption.textContent = label;
    block.appendChild(caption);
    block.appendChild(document.createTextNode(value));
    elements.resultSummary.appendChild(block);
  });

  elements.archiveLink.href = manifest.archive_path;
  elements.resultPanel.hidden = false;
}

async function loadJobs() {
  try {
    const response = await fetch(`${API_BASE}/jobs`);
    if (!response.ok) throw new Error(await readError(response));
    const data = await response.json();

    elements.jobRows.innerHTML = "";
    elements.jobsEmpty.hidden = data.jobs.length > 0;
    data.jobs.forEach((job) => {
      const row = document.createElement("tr");
      const cells = [
        job.job_id,
        job.source_name,
        humanSeconds(job.duration_seconds),
        job.format.toUpperCase(),
        String(job.chunk_count),
        humanBytes(job.output_bytes),
      ];
      cells.forEach((text) => {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.appendChild(cell);
      });

      const actionCell = document.createElement("td");
      const zip = document.createElement("a");
      zip.href = `${API_BASE}/${job.job_id}/archive`;
      zip.textContent = "ZIP";
      const remove = document.createElement("a");
      remove.href = "#";
      remove.textContent = "删除";
      remove.style.marginLeft = "12px";
      remove.addEventListener("click", async (event) => {
        event.preventDefault();
        if (!window.confirm(`删除任务 ${job.job_id}？`)) return;
        const result = await fetch(`${API_BASE}/${job.job_id}`, { method: "DELETE" });
        if (result.ok) {
          setStatus(`已删除 ${job.job_id}`);
          loadJobs();
        } else {
          setStatus(await readError(result), true);
        }
      });
      actionCell.appendChild(zip);
      actionCell.appendChild(remove);
      row.appendChild(actionCell);
      elements.jobRows.appendChild(row);
    });
  } catch (error) {
    setStatus(`历史加载失败：${error.message}`, true);
  }
}

elements.format.addEventListener("change", () => {
  elements.formatHint.textContent = formatLabels[elements.format.value] || "";
});

elements.refresh.addEventListener("click", loadJobs);

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.file.files[0];
  if (!file) {
    setStatus("请先选择音频文件", true);
    return;
  }

  const payload = new FormData();
  payload.append("file", file);
  payload.append("format", elements.format.value);
  payload.append("split", elements.split.value || "0");

  elements.submit.disabled = true;
  setStatus(`上传并转换中：${file.name} (${humanBytes(file.size)})，长录音需要一点时间…`);
  try {
    const response = await fetch(API_BASE, { method: "POST", body: payload });
    if (!response.ok) throw new Error(await readError(response));
    const manifest = await response.json();
    renderResult(manifest);
    setStatus(`完成：${manifest.chunks.length} 个分片，用时 ${manifest.elapsed_seconds}s`);
    loadJobs();
  } catch (error) {
    setStatus(`转换失败：${error.message}`, true);
  } finally {
    elements.submit.disabled = false;
  }
});

loadCapabilities();
loadJobs();
