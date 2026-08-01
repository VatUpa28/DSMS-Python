(() => {
  const root = document.getElementById("holds-workspace");
  if (!root) return;

  const canManage = root.dataset.canManage === "true";
  const selected = new Map();
  let selectedClient = null;
  let submitting = false;
  const byId = (id) => document.getElementById(id);
  const dash = (value) => value === null || value === undefined || value === "" ? "—" : value;
  const money = (value) => {
    const number = Number(value);
    return value === null || value === undefined || value === "" || !Number.isFinite(number)
      ? "—"
      : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(number);
  };

  function showAlert(message, style = "danger") {
    const alert = byId("holds-alert");
    alert.className = `alert alert-${style}`;
    alert.textContent = message;
    alert.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function normalizeStockNumbers(raw) {
    const seen = new Set();
    const values = [];
    raw.split(/[\s,]+/).forEach((part) => {
      const value = part.trim();
      if (value && !seen.has(value)) {
        seen.add(value);
        values.push(value);
      }
    });
    return values;
  }

  async function jsonRequest(url, options = {}) {
    const response = await dsmsFetch(url, options);
    let body = {};
    try { body = await response.json(); } catch (_error) { body = {}; }
    if (!response.ok) throw new Error(body.error || "The request could not be completed.");
    return body;
  }

  function addCell(row, value) {
    const cell = row.insertCell();
    cell.textContent = dash(value);
  }

  function updateSelection() {
    byId("selected-hold-count").textContent = selected.size;
    byId("place-hold").disabled = submitting || !selectedClient || !selected.size;
  }

  function renderAvailable(stones) {
    const body = byId("available-hold-results");
    body.replaceChildren();
    if (!stones.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 14;
      cell.className = "text-center text-muted py-4";
      cell.textContent = "No matching AVAILABLE Stones were found.";
      return;
    }
    stones.forEach((stone) => {
      const row = body.insertRow();
      const selectCell = row.insertCell();
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input";
      checkbox.value = stone.id;
      checkbox.checked = selected.has(stone.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.set(stone.id, stone);
        else selected.delete(stone.id);
        updateSelection();
      });
      selectCell.appendChild(checkbox);
      [stone.stock_number, stone.report_number, stone.lab, stone.shape, stone.weight,
        stone.color, stone.clarity, stone.cut, stone.polish, stone.symmetry,
        stone.fluorescence_intensity].forEach((value) => addCell(row, value));
      addCell(row, money(stone.price_per_carat));
      addCell(row, money(stone.total_price));
    });
  }

  async function searchAvailable() {
    if (!selectedClient) return showAlert("Select a client before searching Stones.");
    const form = byId("available-search");
    const parameters = new URLSearchParams(new FormData(form));
    const stockNumbers = normalizeStockNumbers(parameters.get("stock_numbers") || "");
    byId("hold-stock-count").textContent = stockNumbers.length;
    if (stockNumbers.length > 25) return showAlert("A maximum of 25 stock numbers may be searched at once.");
    parameters.set("stock_numbers", stockNumbers.join(","));
    try {
      const body = await jsonRequest(`/api/holds/available-stones?${parameters}`);
      renderAvailable(body.stones);
    } catch (error) {
      showAlert(error.message);
    }
  }

  async function searchClients() {
    const query = byId("hold-client-search").value.trim();
    if (!query) return showAlert("Enter a client code or client name.");
    try {
      const body = await jsonRequest(`/api/transaction-workspace/clients?q=${encodeURIComponent(query)}`);
      const results = byId("hold-client-results");
      results.replaceChildren();
      if (!body.clients.length) {
        const empty = document.createElement("div");
        empty.className = "text-muted py-2";
        empty.textContent = "No matching clients were found.";
        results.appendChild(empty);
        return;
      }
      body.clients.forEach((client) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.textContent = `${client.code} — ${client.name} — ${client.address || "No address"}`;
        button.addEventListener("click", () => {
          selectedClient = client;
          selected.clear();
          updateSelection();
          byId("selected-hold-client").textContent = button.textContent;
          results.replaceChildren();
          searchAvailable();
        });
        results.appendChild(button);
      });
    } catch (error) {
      showAlert(error.message);
    }
  }

  async function placeHolds() {
    if (submitting || !selectedClient || !selected.size) return;
    submitting = true;
    updateSelection();
    try {
      const result = await jsonRequest("/api/holds", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: selectedClient.id,
          stone_ids: [...selected.keys()],
        }),
      });
      sessionStorage.setItem(
        "holdsNotice",
        `Placed ${result.held} Stone(s) on HOLD: ${result.stock_numbers.join(", ")}`,
      );
      window.location.reload();
    } catch (error) {
      submitting = false;
      updateSelection();
      showAlert(error.message);
    }
  }

  async function releaseHolds() {
    if (submitting) return;
    const ids = [...document.querySelectorAll(".release-hold-item:checked")]
      .map((item) => Number(item.value));
    if (!ids.length) return showAlert("Select at least one held Stone to release.");
    if (!window.confirm("Release the selected HOLDs and make their Stones AVAILABLE?")) return;
    submitting = true;
    byId("release-holds").disabled = true;
    try {
      const result = await jsonRequest("/api/holds/release", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stone_ids: ids }),
      });
      sessionStorage.setItem(
        "holdsNotice",
        `Released ${result.released} HOLD(s): ${result.stock_numbers.join(", ")}`,
      );
      window.location.reload();
    } catch (error) {
      submitting = false;
      byId("release-holds").disabled = false;
      showAlert(error.message);
    }
  }

  const notice = sessionStorage.getItem("holdsNotice");
  if (notice) {
    sessionStorage.removeItem("holdsNotice");
    showAlert(notice, "success");
  }
  if (!canManage) return;
  byId("search-hold-clients").addEventListener("click", searchClients);
  byId("hold-client-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); searchClients(); }
  });
  byId("available-search").addEventListener("submit", (event) => {
    event.preventDefault();
    searchAvailable();
  });
  byId("available-search").elements.stock_numbers.addEventListener("input", (event) => {
    byId("hold-stock-count").textContent = normalizeStockNumbers(event.target.value).length;
  });
  byId("clear-available-search").addEventListener("click", () => {
    byId("available-search").reset();
    byId("hold-stock-count").textContent = "0";
    searchAvailable();
  });
  byId("clear-hold-selection").addEventListener("click", () => {
    selected.clear();
    document.querySelectorAll("#available-hold-results input[type=checkbox]")
      .forEach((item) => { item.checked = false; });
    updateSelection();
  });
  byId("place-hold").addEventListener("click", placeHolds);
  byId("release-holds").addEventListener("click", releaseHolds);
  updateSelection();
})();
