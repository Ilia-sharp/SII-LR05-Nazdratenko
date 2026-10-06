/* ============================================================
   Nazdratenko · Anomaly Detection — логика интерфейса
   Живой прогресс: сайт опрашивает /api/jobs/{id} и показывает
   стадии, процент и журнал — никакого «просто ожидания».
   ============================================================ */

"use strict";

const STAGES = [
  "Приём данных",
  "Парсинг и валидация",
  "Обработка пропусков",
  "Z-score детекция (окно из конфигурации)",
  "Baseline-сравнение",
  "График и артефакты",
  "Готово",
];

const SEV_COLORS = { high: "#E5484D", medium: "#F5A623", low: "#7BE0C8" };
const SEV_LABELS = { high: "высокая", medium: "средняя", low: "низкая" };

const state = {
  mode: "demo",
  file: null,
  jobId: null,
  pollTimer: null,
  chart: null,
  lastLogLen: 0,
};

/* ---------- короткие хелперы ---------- */
const $ = (id) => document.getElementById(id);

function fmtNum(x, digits = 2) {
  if (x === null || x === undefined) return "—";
  return Number(x).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function fmtTs(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function show(el, yes = true) { el.classList.toggle("hidden", !yes); }

function setAlert(msg) {
  const box = $("sourceError");
  box.textContent = msg;
  show(box, Boolean(msg));
}

/* ---------- инициализация ---------- */
async function init() {
  buildStages();
  try {
    const [cfg, health] = await Promise.all([
      fetch("/api/config").then((r) => r.json()),
      fetch("/api/health").then((r) => r.json()),
    ]);
    $("thresholdInput").value = cfg.threshold;
    $("windowInput").value = cfg.window;
    $("thresholdInput").min = cfg.threshold_min;
    $("thresholdInput").max = cfg.threshold_max;
    $("modelBadge").textContent = cfg.model_version;
    if (health.demo_data && health.demo_data.points) {
      $("demoText").textContent =
        `UCI · Household Electric Power Consumption · CC BY 4.0 · ` +
        `${fmtNum(health.demo_data.points, 0)} точек (${fmtTs(health.demo_data.first)} — ${fmtTs(health.demo_data.last)})`;
    }
  } catch (e) {
    console.warn("config/health недоступны:", e);
  }
}

function buildStages() {
  $("stagesList").innerHTML = STAGES.map(
    (name, i) =>
      `<li data-i="${i}"><span class="st-dot"></span><span>${i + 1}. ${name}</span></li>`
  ).join("");
}

/* ---------- переключение режима ---------- */
document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.mode = btn.dataset.mode;
    show($("dropzone"), state.mode === "file");
    show($("demoNote"), state.mode === "demo");
    setAlert("");
  });
});

/* ---------- dropzone ---------- */
const dz = $("dropzone");
const fi = $("fileInput");

dz.addEventListener("click", () => fi.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fi.click(); });
["dragenter", "dragover"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); })
);
dz.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) acceptFile(f);
});
fi.addEventListener("change", () => { if (fi.files[0]) acceptFile(fi.files[0]); });

function acceptFile(f) {
  if (!/\.csv$/i.test(f.name)) {
    setAlert("Нужен файл с расширением .csv");
    return;
  }
  state.file = f;
  dz.classList.add("loaded");
  $("dzTitle").textContent = f.name;
  $("dzHint").textContent = `${(f.size / 1024).toFixed(1)} КБ · нажмите, чтобы заменить`;
  setAlert("");
}

/* ---------- запуск анализа ---------- */
$("runBtn").addEventListener("click", async () => {
  setAlert("");
  if (state.mode === "file" && !state.file) {
    setAlert("Выберите CSV-файл или переключитесь на встроенный ряд.");
    return;
  }

  const fd = new FormData();
  fd.append("mode", state.mode);
  const th = parseFloat($("thresholdInput").value);
  const win = parseInt($("windowInput").value, 10);
  if (!Number.isNaN(th)) fd.append("threshold", th);
  if (!Number.isNaN(win)) fd.append("window", win);
  if (state.mode === "file") fd.append("file", state.file);

  $("runBtn").disabled = true;
  $("runBtn").querySelector(".btn-label").textContent = "Считаю…";

  try {
    const resp = await fetch("/api/run", { method: "POST", body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const { job_id } = await resp.json();
    startPolling(job_id);
  } catch (e) {
    setAlert("Не удалось запустить расчёт: " + e.message);
    resetRunBtn();
  }
});

function resetRunBtn() {
  $("runBtn").disabled = false;
  $("runBtn").querySelector(".btn-label").textContent = "Запустить анализ";
}

/* ---------- прогресс ---------- */
function startPolling(jobId) {
  state.jobId = jobId;
  state.lastLogLen = 0;
  show($("cardProgress"), true);
  show($("cardResults"), false);
  $("logBox").innerHTML = "";
  setProgress(0, "Ставлю задачу в очередь…");
  buildStages();
  $("cardProgress").scrollIntoView({ behavior: "smooth", block: "start" });

  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(poll, 700);
  poll();
}

async function poll() {
  if (!state.jobId) return;
  let job;
  try {
    const resp = await fetch(`/api/jobs/${state.jobId}`);
    if (resp.status === 404) throw new Error("Задача устарела — сервис перезапускался. Запустите анализ снова.");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    job = await resp.json();
  } catch (e) {
    clearInterval(state.pollTimer);
    setProgress(0, "Ошибка опроса: " + e.message);
    resetRunBtn();
    return;
  }

  // журнал
  const box = $("logBox");
  for (; state.lastLogLen < job.log.length; state.lastLogLen++) {
    const line = document.createElement("div");
    line.textContent = job.log[state.lastLogLen];
    box.appendChild(line);
  }
  box.scrollTop = box.scrollHeight;

  // стадии
  setProgress(job.percent || 0, job.stage || "…");
  const idx = job.stage_index || 0;
  document.querySelectorAll("#stagesList li").forEach((li, i) => {
    li.classList.toggle("done", i < idx || job.status === "done");
    li.classList.toggle("active", i === idx && job.status === "running");
  });

  if (job.status === "done") {
    clearInterval(state.pollTimer);
    setProgress(100, "Готово — результаты ниже");
    renderResults(job.result);
    resetRunBtn();
  } else if (job.status === "error") {
    clearInterval(state.pollTimer);
    setProgress(job.percent || 0, "Ошибка: " + (job.error || "неизвестная"));
    resetRunBtn();
  }
}

function setProgress(pct, stageText) {
  $("progressFill").style.width = pct + "%";
  $("progressPct").textContent = pct + "%";
  $("progressStage").textContent = stageText || "";
  $("progressBar").setAttribute("aria-valuenow", pct);
}

/* ---------- результаты ---------- */
function renderResults(r) {
  show($("cardResults"), true);

  $("resultMeta").textContent =
    `${r.model_version} · порог ${r.threshold}σ · окно ${r.window} · ` +
    `${fmtTs(r.stats.first_ts)} — ${fmtTs(r.stats.last_ts)}`;

  // баннеры статуса
  $("statusBanners").innerHTML = (r.status && r.status.notes ? r.status.notes : [])
    .map((n) => `<div class="banner banner-warn">${n}</div>`)
    .join("");

  // KPI
  const st = r.stats;
  $("kpis").innerHTML = [
    kpi(fmtNum(st.points_total, 0), "точек в ряду"),
    kpi(fmtNum(r.scored_points, 0), "проскорено"),
    kpi(fmtNum(r.anomalies_count, 0), `аномалий · ${r.anomaly_rate}%`, "kpi-red"),
    kpi(`${r.threshold}σ`, "порог тревоги", "kpi-blue"),
    kpi(fmtNum(st.gaps_total, 0), `дыр (NaN-точек: ${fmtNum(st.points_missing, 0)})`),
    kpi(`${fmtNum(st.mean)} / ${fmtNum(st.max)}`, "кВт · средний / максимум"),
  ].join("");

  // артефакты
  $("dlCsv").href = r.artifacts.csv;
  $("dlJson").href = r.artifacts.json;
  $("dlPng").href = r.artifacts.png;
  $("dlHtml").href = r.artifacts.html;

  // baseline-таблица
  $("baselineTable").querySelector("tbody").innerHTML = (r.baselines || [])
    .map(
      (b) => `<tr>
        <td>${b.name}</td>
        <td><b>${fmtNum(b.anomalies, 0)}</b></td>
        <td>${fmtNum(b.anomaly_rate)}</td>
        <td>${b.mae === null ? "—" : fmtNum(b.mae, 4)}</td>
        <td>${fmtNum(b.jaccard_with_variant)}</td>
      </tr>`
    )
    .join("");

  // пропуски
  const gapsBody = $("gapsTable").querySelector("tbody");
  gapsBody.innerHTML = (r.gaps || [])
    .map(
      (g) => `<tr>
        <td>${fmtTs(g.start)}</td>
        <td>${fmtTs(g.end)}</td>
        <td>${g.points}</td>
        <td>${g.action === "interpolated"
          ? '<span class="badge b-low">интерполировано</span>'
          : '<span class="badge b-high">оставлено NaN</span>'}</td>
      </tr>`
    )
    .join("");
  if (!(r.gaps || []).length) {
    gapsBody.innerHTML = `<tr><td colspan="4" style="color:var(--muted)">Пропусков нет — ряд регулярный</td></tr>`;
  }

  // тревоги
  $("anCount").textContent = r.anomalies_count;
  const shown = (r.anomalies || []).slice(0, 100);
  $("anomaliesTable").querySelector("tbody").innerHTML = shown
    .map(
      (a) => `<tr>
        <td>${fmtTs(a.timestamp)}</td>
        <td>${fmtNum(a.value, 3)}</td>
        <td>${fmtNum(a.score)}</td>
        <td><span class="badge b-${a.severity}">${a.severity} · ${SEV_LABELS[a.severity]}</span></td>
      </tr>`
    )
    .join("");
  if ((r.anomalies || []).length > shown.length) {
    $("anomaliesTable").querySelector("tbody").insertAdjacentHTML(
      "beforeend",
      `<tr><td colspan="4" style="color:var(--muted)">…и ещё ${r.anomalies_count - shown.length} — полный список в anomalies.csv</td></tr>`
    );
  }

  drawChart(r);
  $("cardResults").scrollIntoView({ behavior: "smooth", block: "start" });
}

function kpi(value, label, extra = "") {
  return `<div class="kpi ${extra}"><b>${value}</b><span>${label}</span></div>`;
}

/* ---------- график ---------- */
function drawChart(r) {
  const ctx = $("chart").getContext("2d");
  if (state.chart) { state.chart.destroy(); state.chart = null; }

  const labels = r.series.map((p) => fmtTs(p.t));
  const values = r.series.map((p) => p.v);
  const anomalies = [];
  r.series.forEach((p, i) => {
    if (p.a) anomalies.push({ x: i, y: p.v, sev: p.sev });
  });

  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Потребление, кВт",
          data: values,
          borderColor: "#5B8DEF",
          backgroundColor: "rgba(91,141,239,0.08)",
          borderWidth: 1.2,
          pointRadius: 0,
          tension: 0.25,
          fill: true,
        },
        {
          type: "scatter",
          label: "Аномалии",
          data: anomalies.map((a) => ({ x: a.x, y: a.y })),
          pointRadius: 5.5,
          pointHoverRadius: 7,
          pointBackgroundColor: anomalies.map((a) => SEV_COLORS[a.sev] || SEV_COLORS.low),
          pointBorderColor: "#fff",
          pointBorderWidth: 1.5,
          showLine: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 700, easing: "easeOutQuart" },
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: {
          labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 8, font: { family: "Manrope", size: 12 } },
        },
        tooltip: {
          callbacks: {
            title: (items) => labels[items[0].dataIndex],
            label: (item) =>
              item.datasetIndex === 0
                ? ` ${fmtNum(item.parsed.y, 3)} кВт`
                : ` Аномалия: ${fmtNum(item.parsed.y, 3)} кВт`,
          },
        },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 9, maxRotation: 0, font: { size: 10.5 }, color: "#6B7A90" },
          grid: { display: false },
        },
        y: {
          title: { display: true, text: "кВт", font: { size: 11 } },
          ticks: { font: { size: 10.5 }, color: "#6B7A90" },
          grid: { color: "#EDF3FA" },
        },
      },
    },
  });
}

init();
