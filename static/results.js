// Donut chart of cost by service, plus print helpers.
(function () {
  // Categorical palette in a fixed, colour-blind-checked order; "Other" is neutral grey.
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const OTHER = "#9a9890";

  const dataEl = document.getElementById("chart-data");
  if (dataEl) {
    const slices = JSON.parse(dataEl.textContent);
    const colors = slices.map((s, i) => (s.label === "Other" ? OTHER : PALETTE[i % PALETTE.length]));
    document.querySelectorAll(".legend-swatch").forEach((el) => {
      el.style.backgroundColor = colors[Number(el.dataset.index)];
    });
    const total = slices.reduce((sum, s) => sum + s.cost, 0);
    let money;
    try {
      money = new Intl.NumberFormat("en-US", { style: "currency", currency: dataEl.dataset.currency || "USD" });
    } catch (e) {
      money = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    if (window.Chart) {
      new Chart(document.getElementById("service-chart"), {
        type: "doughnut",
        data: {
          labels: slices.map((s) => s.label),
          datasets: [{
            data: slices.map((s) => s.cost),
            backgroundColor: colors,
            borderColor: "#ffffff",
            borderWidth: 2,
            hoverOffset: 6,
          }],
        },
        options: {
          cutout: "68%",
          maintainAspectRatio: true,
          plugins: {
            legend: { display: false },  // the list next to the chart is the legend
            tooltip: {
              callbacks: {
                label: (ctx) => ` ${money.format(ctx.parsed)} (${Math.round((100 * ctx.parsed) / total)}%)`,
              },
            },
          },
        },
      });
    }
  }

  // Print the full report: open every charge so the technical details are included.
  let opened = [];
  window.addEventListener("beforeprint", () => {
    opened = [...document.querySelectorAll("details.charge:not([open])")];
    opened.forEach((d) => (d.open = true));
  });
  window.addEventListener("afterprint", () => opened.forEach((d) => (d.open = false)));
})();
