// Sequoia-X Pages · 前端
// 负责：Chart.js 折线图渲染 + 共振榜 hover 联动

(function () {
  "use strict";

  // ── 工具 ──
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else e.setAttribute(k, v);
    });
    if (children) children.forEach(c => e.appendChild(c));
    return e;
  }

  // ── 折线图：每日各策略选中数趋势 ──
  function renderHistoryChart(canvas, history) {
    if (!history || !history.length || typeof Chart === "undefined") return;

    const labels = history.map(h => h.date);
    // 收集所有出现过的策略 key
    const strategyKeys = new Set();
    history.forEach(h => Object.keys(h.strategies || {}).forEach(k => strategyKeys.add(k)));
    const keys = Array.from(strategyKeys);

    // Polymarket 风调色板
    const palette = ["#5fa8ff", "#b48cff", "#19d27a", "#ffb547", "#ff5577", "#4dd1e1", "#ff7e7e", "#7c8fff"];

    const datasets = keys.map((k, i) => ({
      label: k,
      data: history.map(h => (h.strategies[k] || []).length),
      borderColor: palette[i % palette.length],
      backgroundColor: palette[i % palette.length] + "22",
      tension: 0.25,
      borderWidth: 2,
      pointRadius: 2,
      pointHoverRadius: 5,
    }));

    // 总数趋势
    datasets.unshift({
      label: "全市场去重",
      data: history.map(h => h.total || 0),
      borderColor: "#e7ecf5",
      backgroundColor: "#e7ecf522",
      tension: 0.3,
      borderWidth: 2.5,
      borderDash: [5, 3],
      pointRadius: 3,
      pointHoverRadius: 6,
    });

    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#97a3bf", font: { size: 11 } } },
          tooltip: { backgroundColor: "#0b1020", borderColor: "#232b40", borderWidth: 1 },
        },
        scales: {
          x: { ticks: { color: "#97a3bf", font: { size: 10 }, maxTicksLimit: 12 }, grid: { color: "#232b40" } },
          y: { beginAtZero: true, ticks: { color: "#97a3bf", precision: 0 }, grid: { color: "#232b40" } },
        },
      },
    });
  }

  // ── 共振榜悬停联动 ──
  function bindResonance() {
    document.querySelectorAll(".resonance-item").forEach(item => {
      item.addEventListener("click", () => {
        const code = item.dataset.code;
        if (!code) return;
        const target = document.querySelector(`[data-strategy-for="${code}"]`);
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.style.outline = "2px solid #b48cff";
          setTimeout(() => target.style.outline = "", 1200);
        }
      });
    });
  }

  // ── 启动 ──
  document.addEventListener("DOMContentLoaded", () => {
    bindResonance();
    const canvas = document.getElementById("chart-history");
    if (canvas && canvas.dataset.history) {
      try {
        renderHistoryChart(canvas, JSON.parse(canvas.dataset.history));
      } catch (e) {
        console.error("history chart parse failed:", e);
      }
    }
  });
})();