let currencyRates = { EUR: 1, USD: 1.14, INR: 97.3 };
let currency = "EUR";
let dashboard = null;
let showAll = false;
let activePage = "overview";
let holdingSearch = "";
let marketFilter = "All";
let holdingSort = "value_desc";
let chartRange = "1Y";
let categoryFilter = "All";
let capFilter = "All";
let barbellFilter = "All";

const money = value => new Intl.NumberFormat("en-US", {
  style: "currency", currency, maximumFractionDigits: 0
}).format(value * currencyRates[currency]);

const nativeMoney = (value, sourceCurrency) => value == null ? "—" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: sourceCurrency, maximumFractionDigits: sourceCurrency === "INR" ? 0 : 2
}).format(value);
const decimal = value => value == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(value);

function renderGreeting() {
  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  document.getElementById("currentDate").textContent = now.toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric"
  });
  document.getElementById("greeting").textContent = `${greeting}, Kiran`;
}

function scopedHoldings() {
  return dashboard.holdings.filter(asset => marketFilter === "All" || asset.market === marketFilter);
}

function filteredHoldings() {
  const term = holdingSearch.toLowerCase();
  const holdings = scopedHoldings().filter(asset =>
    (categoryFilter === "All" || asset.asset_category === categoryFilter) &&
    (capFilter === "All" || asset.cap_bucket === capFilter) &&
    (barbellFilter === "All" || asset.barbell_role === barbellFilter) &&
    (!term || asset.name.toLowerCase().includes(term) || asset.ticker.toLowerCase().includes(term))
  );
  return holdings.sort((left, right) => {
    if (holdingSort === "value_asc") return left.value_eur - right.value_eur;
    if (holdingSort === "pnl_desc") return (right.value_eur - right.invested_eur) - (left.value_eur - left.invested_eur);
    if (holdingSort === "pnl_asc") return (left.value_eur - left.invested_eur) - (right.value_eur - right.invested_eur);
    if (holdingSort === "return_desc") return right.return_percent - left.return_percent;
    if (holdingSort === "return_asc") return left.return_percent - right.return_percent;
    if (holdingSort === "invested_desc") return right.invested_eur - left.invested_eur;
    if (holdingSort === "invested_asc") return left.invested_eur - right.invested_eur;
    if (holdingSort === "weight_desc") return right.value_eur - left.value_eur;
    if (holdingSort === "quantity_desc") return (right.quantity || 0) - (left.quantity || 0);
    if (holdingSort === "price_desc") return (right.current_price || 0) - (left.current_price || 0);
    if (holdingSort === "market_asc") return left.market.localeCompare(right.market) || right.value_eur - left.value_eur;
    if (holdingSort === "name_asc") return left.name.localeCompare(right.name);
    return right.value_eur - left.value_eur;
  });
}

function scopedSummary() {
  const holdings = scopedHoldings();
  const netWorth = holdings.reduce((total, asset) => total + asset.value_eur, 0);
  const invested = holdings.reduce((total, asset) => total + asset.invested_eur, 0);
  const pnl = netWorth - invested;
  return { netWorth, invested, pnl, returnPercent: invested ? pnl / invested * 100 : 0 };
}

function filteredSummary() {
  const holdings = filteredHoldings();
  const netWorth = holdings.reduce((total, asset) => total + asset.value_eur, 0);
  const invested = holdings.reduce((total, asset) => total + asset.invested_eur, 0);
  const pnl = netWorth - invested;
  return { netWorth, invested, pnl, returnPercent: invested ? pnl / invested * 100 : 0 };
}

function renderPortfolioSummary() {
  const summary = filteredSummary();
  document.getElementById("portfolioScope").textContent = "FILTERED HOLDINGS";
  document.getElementById("filteredNetWorth").textContent = money(summary.netWorth);
  document.getElementById("filteredInvested").textContent = money(summary.invested);
  document.getElementById("filteredPnl").textContent = money(summary.pnl);
  document.getElementById("filteredPnl").className = summary.pnl >= 0 ? "positive" : "negative";
  document.getElementById("filteredReturn").textContent = `${summary.returnPercent >= 0 ? "+" : ""}${summary.returnPercent.toFixed(1)}%`;
  document.getElementById("filteredReturn").className = summary.returnPercent >= 0 ? "positive" : "negative";
}

function renderHeadlineSummary() {
  const summary = scopedSummary();
  document.getElementById("netWorth").textContent = money(summary.netWorth);
  document.getElementById("dayChange").textContent = dashboard.latest_refresh ? "Delayed quotes refreshed" : "Imported values";
  document.getElementById("returns").textContent = (summary.pnl >= 0 ? "+" : "") + money(summary.pnl);
  document.getElementById("invested").textContent = money(summary.invested);
}

function renderHoldings() {
  const filtered = filteredHoldings();
  const holdings = showAll ? filtered : filtered.slice(0, 6);
  const portfolioValue = filteredSummary().netWorth;
  document.getElementById("holdingsCount").textContent = `${filtered.length} holdings`;
  document.getElementById("holdingsBody").innerHTML = holdings.map(asset => `<tr>
    <td class="asset-cell"><span class="asset-logo">${asset.ticker.slice(0, 1)}</span><span><strong>${asset.name}</strong><small>${asset.ticker}</small></span></td>
    <td><span class="market-badge">${asset.market}</span></td>
    <td>${asset.asset_category}</td>
    <td>${asset.cap_bucket}</td>
    <td><span class="market-badge" title="${asset.barbell_reason}">${asset.barbell_role}</span></td>
    <td>${decimal(asset.quantity)}</td>
    <td>${nativeMoney(asset.average_cost, asset.source_currency)}</td>
    <td>${nativeMoney(asset.current_price, asset.source_currency)}</td>
    <td><strong>${money(asset.invested_eur)}</strong></td>
    <td><strong>${money(asset.value_eur)}</strong></td>
    <td class="${asset.value_eur - asset.invested_eur >= 0 ? "positive" : "negative"}"><strong>${money(asset.value_eur - asset.invested_eur)}</strong></td>
    <td class="${asset.return_percent >= 0 ? "positive" : "negative"}"><strong>${asset.return_percent >= 0 ? "+" : ""}${asset.return_percent}%</strong></td>
    <td>${portfolioValue ? Math.round(asset.value_eur / portfolioValue * 100) : 0}%</td>
  </tr>`).join("");
}

function renderDashboard() {
  renderStatus();
  renderHeadlineSummary();
  renderHealth();
  renderAllocations();
  renderInsights();
  renderChart();
  renderPortfolioSummary();
  renderHoldings();
}

function renderStatus() {
  currencyRates = { ...currencyRates, ...dashboard.fx_rates };
  const lastUpdated = dashboard.last_updated_at ? new Date(dashboard.last_updated_at) : null;
  document.getElementById("lastUpdated").textContent = lastUpdated
    ? `Portfolio data updated ${lastUpdated.toLocaleString()}`
    : "Portfolio update time is not available";
  const refresh = dashboard.latest_refresh;
  document.getElementById("refreshCoverage").textContent = refresh
    ? `Delayed quotes: ${refresh.refreshed} refreshed · ${refresh.skipped} FX-only · ${refresh.failed} failed · EUR/INR ${refresh.fx_rate_inr.toFixed(4)}`
    : "Quotes have not been refreshed yet. Imported values are shown.";
  const sourceLabels = {
    transactions: "Global transactions",
    india_snapshot: "India stock snapshot",
    manual_mutual_funds_screenshot: "India mutual funds"
  };
  document.getElementById("importHistory").innerHTML = dashboard.imports.map(item => `<div class="import-row">
    <strong>${sourceLabels[item.source] || item.source}</strong><span>${item.total_rows} rows · ${new Date(item.created_at).toLocaleDateString()}</span>
  </div>`).join("") || `<div class="import-row"><span>No imports recorded yet.</span></div>`;
}

async function refreshPrices() {
  const button = document.getElementById("refreshPricesBtn");
  button.disabled = true;
  button.textContent = "Refreshing...";
  try {
    const response = await fetch("/api/prices/refresh", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not refresh prices");
    await loadDashboard();
    showToast(`Prices refreshed: ${result.refreshed} updated, ${result.skipped} skipped, ${result.failed} failed`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "↻ Refresh prices";
  }
}

function renderChart() {
  const scope = marketFilter === "All" ? "All" : marketFilter;
  const ranges = { "1M": 31, "3M": 93, "6M": 186, "1Y": 366, "ALL": Infinity };
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - ranges[chartRange]);
  const points = dashboard.snapshots
    .filter(item => item.market === scope && (chartRange === "ALL" || new Date(item.snapshot_date) >= cutoff))
    .sort((left, right) => left.snapshot_date.localeCompare(right.snapshot_date));
  const empty = document.getElementById("chartEmpty");
  const chart = document.querySelector(".line-chart");
  const labels = document.getElementById("chartLabels");
  if (points.length < 2) {
    chart.style.display = "none";
    labels.style.display = "none";
    empty.style.display = "block";
    return;
  }
  chart.style.display = "block";
  labels.style.display = "flex";
  empty.style.display = "none";
  const values = points.map(point => point.net_worth_eur);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const coordinates = points.map((point, index) => ({
    x: points.length === 1 ? 500 : index / (points.length - 1) * 1000,
    y: 205 - ((point.net_worth_eur - min) / spread * 165)
  }));
  const line = coordinates.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  document.getElementById("chartLine").setAttribute("d", line);
  document.getElementById("chartArea").setAttribute("d", `${line} L1000 230 L0 230Z`);
  const last = coordinates[coordinates.length - 1];
  document.getElementById("chartPoint").setAttribute("cx", last.x);
  document.getElementById("chartPoint").setAttribute("cy", last.y);
  labels.innerHTML = points.map(point => `<span>${new Date(`${point.snapshot_date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>`).join("");
}

function renderHealth() {
  const health = dashboard.health;
  document.getElementById("healthScore").textContent = health.score;
  document.getElementById("healthLabel").textContent = health.label;
  document.getElementById("healthSignal").textContent = health.signals[0] || "Your portfolio is well diversified. Add another market when you are ready.";
  document.getElementById("healthRing").style.strokeDashoffset = 232 - (232 * health.score / 100);
}

function renderAllocations() {
  const geo = dashboard.summary.geography;
  const indiaEnd = geo.India;
  document.getElementById("geographyDonut").style.background = `conic-gradient(var(--green) 0 ${indiaEnd}%,var(--navy) ${indiaEnd}% 100%)`;
  document.getElementById("marketCount").textContent = Object.values(geo).filter(value => value > 0).length;
  document.getElementById("indiaAllocation").textContent = `${geo.India}%`;
  document.getElementById("globalAllocation").textContent = `${geo.Global}%`;
  const assetClasses = dashboard.summary.asset_categories;
  [
    ["Stock", "stocks"],
    ["Mutual fund", "mutualFunds"],
    ["ETF", "etfs"],
    ["Other", "other"]
  ].forEach(([name, id]) => {
    const value = assetClasses[name];
    document.getElementById(`${id}Allocation`).textContent = `${value}%`;
    document.getElementById(`${id}Bar`).style.width = `${value}%`;
  });
  const barbellRoles = dashboard.summary.barbell_roles;
  [
    ["Core", "barbellCore"],
    ["Upside", "barbellUpside"],
    ["Review", "barbellReview"]
  ].forEach(([name, id]) => {
    const value = barbellRoles[name];
    document.getElementById(`${id}Allocation`).textContent = `${value}%`;
    document.getElementById(`${id}Bar`).style.width = `${value}%`;
  });
  const capBuckets = dashboard.summary.cap_buckets;
  [
    ["Large cap", "largeCap"],
    ["Mid cap", "midCap"],
    ["Small cap", "smallCap"],
    ["Unclassified", "unclassifiedCap"]
  ].forEach(([name, id]) => {
    const value = capBuckets[name];
    document.getElementById(`${id}Allocation`).textContent = `${value}%`;
    document.getElementById(`${id}Bar`).style.width = `${value}%`;
  });
}

function renderInsights() {
  const icons = { warning: "!", good: "↗", info: "€" };
  document.getElementById("insightsList").innerHTML = dashboard.suggestions.map(insight => `<div class="insight">
    <span class="insight-icon ${insight.level}">${icons[insight.level]}</span>
    <div><strong>${insight.title}</strong><p>${insight.description}</p><button>${insight.action} <span>→</span></button></div>
  </div>`).join("");
}

async function loadDashboard() {
  const response = await fetch("/api/dashboard");
  if (!response.ok) throw new Error("Could not load portfolio dashboard");
  dashboard = await response.json();
  renderDashboard();
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3200);
}

function setPage(page) {
  activePage = page;
  showAll = page === "holdings";
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll(".overview-only").forEach(section => section.hidden = page === "holdings");
  document.getElementById("portfolioContent").classList.toggle("holdings-view", page === "holdings");
  document.getElementById("holdingsTitle").textContent = page === "holdings" ? `${marketFilter === "All" ? "All" : marketFilter} holdings` : "Top holdings";
  document.getElementById("viewAllBtn").hidden = page === "holdings";
  document.querySelectorAll(".portfolio-link").forEach(item => item.classList.toggle("selected", page === "holdings" && item.dataset.market === marketFilter));
  renderHeadlineSummary();
  renderPortfolioSummary();
  renderChart();
  renderHoldings();
}

document.getElementById("currencySelect").addEventListener("change", event => {
  currency = event.target.value;
  renderDashboard();
});
document.getElementById("refreshPricesBtn").onclick = refreshPrices;
document.getElementById("addHoldingBtn").onclick = () => document.getElementById("modalBackdrop").classList.add("show");
document.getElementById("closeModal").onclick = () => document.getElementById("modalBackdrop").classList.remove("show");
document.getElementById("modalBackdrop").addEventListener("click", event => {
  if (event.target.id === "modalBackdrop") event.target.classList.remove("show");
});
document.getElementById("viewAllBtn").onclick = event => {
  marketFilter = "All";
  document.getElementById("marketFilter").value = marketFilter;
  setPage("holdings");
};
document.querySelectorAll(".nav-item").forEach(item => item.onclick = () => {
  if (item.dataset.page === "overview" || item.dataset.page === "holdings") {
    if (item.dataset.page === "holdings") {
      marketFilter = "All";
      document.getElementById("marketFilter").value = marketFilter;
    }
    setPage(item.dataset.page);
  }
});
document.querySelectorAll(".portfolio-link").forEach(item => item.onclick = () => {
  marketFilter = item.dataset.market;
  document.getElementById("marketFilter").value = marketFilter;
  setPage("holdings");
});
document.getElementById("holdingSearch").addEventListener("input", event => {
  holdingSearch = event.target.value;
  renderPortfolioSummary();
  renderHoldings();
});
document.getElementById("marketFilter").addEventListener("change", event => {
  marketFilter = event.target.value;
  document.querySelectorAll(".portfolio-link").forEach(item => item.classList.toggle("selected", item.dataset.market === marketFilter));
  document.getElementById("holdingsTitle").textContent = `${marketFilter === "All" ? "All" : marketFilter} holdings`;
  renderHeadlineSummary();
  renderPortfolioSummary();
  renderChart();
  renderHoldings();
});
document.getElementById("categoryFilter").addEventListener("change", event => {
  categoryFilter = event.target.value;
  const capSelect = document.getElementById("capFilter");
  capSelect.disabled = categoryFilter !== "All" && categoryFilter !== "Stock";
  if (capSelect.disabled) {
    capFilter = "All";
    capSelect.value = "All";
  }
  renderPortfolioSummary();
  renderHoldings();
});
document.getElementById("capFilter").addEventListener("change", event => {
  capFilter = event.target.value;
  renderPortfolioSummary();
  renderHoldings();
});
document.getElementById("barbellFilter").addEventListener("change", event => {
  barbellFilter = event.target.value;
  renderPortfolioSummary();
  renderHoldings();
});
document.getElementById("holdingSort").addEventListener("change", event => {
  holdingSort = event.target.value;
  renderHoldings();
});
document.getElementById("importCsvBtn").onclick = () => document.getElementById("csvFileInput").click();
document.getElementById("csvFileInput").addEventListener("change", async event => {
  const [file] = event.target.files;
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  const response = await fetch("/api/holdings/import", { method: "POST", body: data });
  const result = await response.json();
  if (!response.ok) {
    const detail = typeof result.detail === "string" ? result.detail : result.detail?.message;
    showToast(detail || "Could not import CSV file");
    event.target.value = "";
    return;
  }
  await loadDashboard();
  showToast(`CSV imported: ${result.imported} added, ${result.updated} updated`);
  event.target.value = "";
});
document.getElementById("holdingForm").addEventListener("submit", async event => {
  event.preventDefault();
  const data = new FormData(event.target);
  const response = await fetch("/api/holdings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: data.get("name"),
      ticker: data.get("ticker").toUpperCase(),
      market: data.get("market"),
      value_eur: Number(data.get("value")) / currencyRates[currency],
      return_percent: Number(data.get("return")),
      asset_class: "Equities"
    })
  });
  if (!response.ok) throw new Error("Could not add holding");
  await loadDashboard();
  event.target.reset();
  document.getElementById("modalBackdrop").classList.remove("show");
  showToast("Holding added to your portfolio");
});
document.querySelectorAll(".range-tabs button").forEach(button => button.onclick = () => {
  document.querySelector(".range-tabs .active").classList.remove("active");
  button.classList.add("active");
  chartRange = button.textContent;
  renderChart();
});

renderGreeting();
loadDashboard().catch(error => {
  console.error(error);
  document.getElementById("holdingsBody").innerHTML = `<tr><td colspan="13">Start the FastAPI server to load your portfolio.</td></tr>`;
});
