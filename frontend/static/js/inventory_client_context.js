(() => {
  "use strict";
  const root = document.getElementById("inventory-client-context");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const canMemo = root.dataset.canCreateMemo === "true";
  const canConvert = root.dataset.canConvert === "true";
  let client = null;
  let memoSelection = new Map();
  let invoiceSelection = new Map();
  let sourceMemo = null;

  const money = (value) => value === null || value === "" || !Number.isFinite(Number(value))
    ? "—" : Number(value).toLocaleString("en-US", { style: "currency", currency: "USD" });
  const text = (value) => value === null || value === "" ? "—" : String(value);
  const requestJson = async (url) => {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "The request could not be completed.");
    return body;
  };
  function alert(message, type = "danger") {
    const box = $("context-alert");
    box.textContent = message; box.className = `alert alert-${type}`;
  }
  function clearAlert() { $("context-alert").className = "alert d-none"; }
  function clearSelection() {
    memoSelection.clear(); invoiceSelection.clear(); sourceMemo = null;
    document.querySelectorAll(".context-select").forEach((input) => { input.checked = false; input.disabled = false; });
    renderSelection();
  }
  function renderSelection() {
    const selection = memoSelection.size ? memoSelection : invoiceSelection;
    $("context-selection").classList.toggle("d-none", !selection.size);
    if (!selection.size) return;
    let weight = 0; let total = 0; let priced = 0;
    selection.forEach((item) => {
      if (Number.isFinite(Number(item.weight))) weight += Number(item.weight);
      if (item.total_price !== null && Number.isFinite(Number(item.total_price))) { total += Number(item.total_price); priced += 1; }
    });
    const invoiceMode = invoiceSelection.size > 0;
    $("context-selection-label").textContent = invoiceMode
      ? `Create Invoice from ${sourceMemo.memo_number} — ${selection.size} Stones`
      : `Create Memo — ${selection.size} Stones`;
    $("context-selection-totals").textContent = `${weight.toFixed(2)} ct · ${money(total)}${priced < selection.size ? " · missing prices excluded" : ""}`;
    $("context-action").textContent = invoiceMode ? "Create Invoice" : "Create Memo";
    document.querySelectorAll(".memo-item-select").forEach((input) => {
      input.disabled = invoiceMode && Number(input.dataset.memoId) !== sourceMemo.memo_id;
    });
  }
  function checkbox(item, mode, memo) {
    const input = document.createElement("input");
    input.type = "checkbox"; input.className = `form-check-input context-select ${mode === "invoice" ? "memo-item-select" : ""}`;
    if (memo) input.dataset.memoId = memo.memo_id;
    input.addEventListener("change", () => {
      if (mode === "memo") {
        if (input.checked && invoiceSelection.size && !confirm("Clear the Memo-item selection and select Stones for a new Memo?")) { input.checked = false; return; }
        if (input.checked && invoiceSelection.size) clearSelection();
        input.checked ? memoSelection.set(item.id, item) : memoSelection.delete(item.id);
      } else {
        if (input.checked && memoSelection.size && !confirm("Clear the Stone selection and select Memo items for conversion?")) { input.checked = false; return; }
        if (input.checked && memoSelection.size) clearSelection();
        if (input.checked && sourceMemo && sourceMemo.memo_id !== memo.memo_id) { input.checked = false; alert("Create one Invoice per source Memo.", "warning"); return; }
        if (input.checked) { sourceMemo = memo; invoiceSelection.set(item.id, item); }
        else { invoiceSelection.delete(item.id); if (!invoiceSelection.size) sourceMemo = null; }
      }
      renderSelection();
    });
    return input;
  }
  function td(row, value) { const cell = row.insertCell(); cell.textContent = text(value); return cell; }
  function renderStoneRows(stones, bodyId, held) {
    const body = $(bodyId); body.replaceChildren();
    stones.forEach((stone) => {
      const row = body.insertRow(); const pick = row.insertCell();
      if (canMemo) pick.appendChild(checkbox(stone, "memo")); else pick.textContent = "—";
      td(row, stone.stock_number);
      if (held) td(row, "Reserved for this client");
      td(row, stone.lab); td(row, stone.shape); td(row, stone.weight); td(row, stone.color); td(row, stone.clarity);
      if (!held) td(row, money(stone.price_per_carat));
      td(row, money(stone.total_price));
    });
    if (!stones.length) { const row = body.insertRow(); const cell = row.insertCell(); cell.colSpan = held ? 9 : 9; cell.className = "text-muted text-center py-3"; cell.textContent = "No matching Stones."; }
  }
  function renderMemoGroups(groups) {
    const host = $("memo-context-groups"); host.replaceChildren();
    groups.forEach((memo) => {
      const section = document.createElement("section"); section.className = "mb-4";
      const heading = document.createElement("h3"); heading.className = "h6";
      heading.textContent = `${memo.memo_number} · ${memo.memo_date} · ${memo.items.length} active item(s)`; section.appendChild(heading);
      const wrap = document.createElement("div"); wrap.className = "table-responsive";
      const table = document.createElement("table"); table.className = "table table-sm table-hover";
      table.innerHTML = "<thead><tr><th>Select</th><th>Stock</th><th>Item status</th><th>Lab</th><th>Shape</th><th>Weight</th><th>Color</th><th>Clarity</th><th>Total</th></tr></thead>";
      const body = table.createTBody();
      memo.items.forEach((item) => { const row = body.insertRow(); const pick = row.insertCell(); if (canConvert) pick.appendChild(checkbox(item, "invoice", memo)); else pick.textContent = "—"; [item.stock_number, item.status, item.lab, item.shape, item.weight, item.color, item.clarity, money(item.total_price)].forEach((value) => td(row, value)); });
      wrap.appendChild(table); section.appendChild(wrap); host.appendChild(section);
    });
    if (!groups.length) host.textContent = "No active Memo items for this client.";
  }
  function params() {
    const map = [["ctx-stock","stock_number"],["ctx-stock-numbers","stock_numbers"],["ctx-memo","memo_number"],["ctx-lab","lab"],["ctx-shape","shape"],["ctx-min-weight","min_weight"],["ctx-max-weight","max_weight"],["ctx-color","color"],["ctx-clarity","clarity"],["ctx-cut","cut"],["ctx-polish","polish"],["ctx-symmetry","symmetry"],["ctx-fluorescence","fluorescence"]];
    const result = new URLSearchParams(); map.forEach(([id,key]) => { if ($(id).value.trim()) result.set(key, $(id).value.trim()); }); return result;
  }
  async function loadContext() {
    clearAlert();
    try {
      const data = await requestJson(`/api/inventory/client-context/${client.id}?${params()}`);
      $("client-context-results").classList.remove("d-none");
      document.querySelectorAll(".inventory-table > form, .inventory-table > .table-responsive, .inventory-table > p").forEach((node) => { node.classList.add("d-none"); });
      const panel = $("selected-client-panel"); panel.replaceChildren();
      const line = document.createElement("div"); line.className = "d-flex justify-content-between";
      const details = document.createElement("div");
      const heading = document.createElement("strong"); heading.textContent = `${text(data.client.code)} — ${text(data.client.name)}`;
      const address = document.createElement("span"); address.className = "text-muted"; address.textContent = text(data.client.address);
      const counts = document.createElement("small"); counts.textContent = `${data.contacts.length} contact(s) · ${data.shipping_addresses.length} shipping address(es) · ${data.active_memo_count} active Memo(s) · ${data.held_stone_count} held Stone(s)`;
      const businessIds = document.createElement("small"); businessIds.className = "d-block text-muted";
      businessIds.textContent = [data.client.polygon_id ? `Polygon ${data.client.polygon_id}` : "", data.client.jbt_id ? `JBT ${data.client.jbt_id}` : "", data.client.rapnet_id ? `RapNet ${data.client.rapnet_id}` : ""].filter(Boolean).join(" · ");
      details.append(heading, document.createElement("br"), address, document.createElement("br"), counts, businessIds);
      const actions = document.createElement("div");
      const detailLink = document.createElement("a"); detailLink.className = "btn btn-sm btn-outline-primary me-1"; detailLink.href = `/clients/${data.client.id}/contacts`; detailLink.textContent = "Client Details";
      const clearButton = document.createElement("button"); clearButton.className = "btn btn-sm btn-outline-secondary"; clearButton.type = "button"; clearButton.textContent = "Clear Client"; clearButton.addEventListener("click", clearClient);
      actions.append(detailLink, clearButton); line.append(details, actions); panel.appendChild(line);
      panel.classList.remove("d-none");
      $("available-context-count").textContent = data.available_stones.length; $("held-context-count").textContent = data.held_stones.length;
      $("memo-context-count").textContent = data.memo_groups.reduce((sum, memo) => sum + memo.items.length, 0);
      renderStoneRows(data.available_stones, "available-context-body", false); renderStoneRows(data.held_stones, "held-context-body", true); renderMemoGroups(data.memo_groups); renderSelection();
    } catch (error) { alert(error.message); }
  }
  function clearClient() {
    if ((memoSelection.size || invoiceSelection.size) && !confirm("Changing clients clears the selected Stones. Continue?")) return;
    client = null; clearSelection(); $("selected-client-panel").classList.add("d-none"); $("client-context-results").classList.add("d-none");
    document.querySelectorAll(".inventory-table > form, .inventory-table > .table-responsive, .inventory-table > p").forEach((node) => { node.classList.remove("d-none"); });
  }
  async function searchClients() {
    const query = $("context-client-search").value.trim(); if (!query) return alert("Enter client information to search.");
    clearAlert();
    try {
      const data = await requestJson(`/api/transaction-workspace/clients?q=${encodeURIComponent(query)}`); const host = $("context-client-results"); host.replaceChildren();
      data.clients.forEach((result) => { const button = document.createElement("button"); button.type="button"; button.className="list-group-item list-group-item-action"; const match = result.matched_contact ? `Matched contact: ${text(result.matched_contact.name)}, ${text(result.matched_contact.phone || result.matched_contact.email)}` : result.matched_shipping ? `Matched shipping: ${text(result.matched_shipping.manager || result.matched_shipping.label)}, ${text(result.matched_shipping.city)}` : "Matched client record"; button.textContent=`${result.code} — ${result.name}\n${result.address}\n${match}`; button.style.whiteSpace="pre-line"; button.addEventListener("click", () => { if ((memoSelection.size || invoiceSelection.size) && !confirm("Changing clients clears the current selection. Continue?")) return; clearSelection(); client=result; host.replaceChildren(); loadContext(); }); host.appendChild(button); });
      if (!data.clients.length) host.textContent = "No matching clients were found.";
    } catch (error) { alert(error.message); }
  }
  $("context-client-search-button").addEventListener("click", searchClients); $("context-client-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); searchClients(); } });
  $("context-apply").addEventListener("click", loadContext);
  $("context-clear-filters").addEventListener("click", () => { ["ctx-stock","ctx-stock-numbers","ctx-memo","ctx-lab","ctx-shape","ctx-min-weight","ctx-max-weight","ctx-color","ctx-clarity","ctx-cut","ctx-polish","ctx-symmetry","ctx-fluorescence"].forEach((id) => { $(id).value=""; }); loadContext(); });
  $("context-clear-selection").addEventListener("click", clearSelection);
  $("context-action").addEventListener("click", () => { if (memoSelection.size) window.location.assign(`/transactions/new?type=memo&client_id=${client.id}&stone_ids=${[...memoSelection.keys()].join(",")}`); else if (invoiceSelection.size) window.location.assign(`/transactions/${sourceMemo.memo_id}/convert-to-invoice?item_ids=${[...invoiceSelection.keys()].join(",")}`); });
})();
