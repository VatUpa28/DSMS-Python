(() => {
  const root = document.getElementById("transaction-workspace");
  if (!root) return;

  const form = document.getElementById("transaction-form");
  const selected = new Map();
  let clientContext = null;
  let transactionType = root.dataset.defaultType;
  let dirty = false;
  let submitting = false;

  const byId = (id) => document.getElementById(id);
  const value = (id) => byId(id)?.value.trim() || "";
  const dash = (input) => input === null || input === undefined || input === "" ? "—" : input;
  const money = (input) => {
    const number = Number(input);
    return input === null || input === undefined || input === "" || !Number.isFinite(number)
      ? "—"
      : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(number);
  };
  const isHold = (stone) => ["H", "HOLD"].includes(stone.status);

  function alertUser(message, style = "danger") {
    const alert = byId("workspace-alert");
    alert.className = `alert alert-${style}`;
    alert.textContent = message;
    alert.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function clearAlert() {
    byId("workspace-alert").className = "alert d-none";
    byId("workspace-alert").textContent = "";
  }

  async function jsonRequest(url, options = {}) {
    const response = await dsmsFetch(url, options);
    let body = {};
    try {
      body = await response.json();
    } catch (_error) {
      body = {};
    }
    if (!response.ok) throw new Error(body.error || "The request could not be completed.");
    return body;
  }

  function setType(type, markDirty = true) {
    transactionType = type;
    document.querySelectorAll(".transaction-type").forEach((button) => {
      const active = button.dataset.transactionType === type;
      button.classList.toggle("btn-primary", active);
      button.classList.toggle("btn-outline-primary", !active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll(".memo-action").forEach((item) => {
      item.classList.toggle("d-none", type !== "memo");
    });
    document.querySelectorAll(".invoice-action").forEach((item) => {
      item.classList.toggle("d-none", type !== "invoice");
    });
    if (markDirty) dirty = true;
  }

  function normalizeStockNumbers(raw) {
    const seen = new Set();
    const values = [];
    raw.split(/[\s,]+/).forEach((part) => {
      const number = part.trim();
      if (number && !seen.has(number)) {
        seen.add(number);
        values.push(number);
      }
    });
    return values;
  }

  function updateStockNumberCount() {
    byId("stock-number-count").textContent =
      normalizeStockNumbers(byId("stock-numbers").value).length;
  }

  function option(label, optionValue = "") {
    const item = document.createElement("option");
    item.value = String(optionValue);
    item.textContent = label;
    return item;
  }

  function resetContactAndAddress() {
    byId("source-contact-id").value = "";
    byId("saved-contact").replaceChildren(option("Manual contact"));
    byId("saved-contact").disabled = true;
    ["person", "phone", "fax"].forEach((id) => { byId(id).value = ""; });

    byId("source-shipping-address-id").value = "";
    byId("saved-address").replaceChildren(option("Manual Ship To"));
    byId("saved-address").disabled = true;
    [
      "ship-to-label", "ship-to-manager", "ship-to-store-number", "ship-to-address",
      "ship-to-city", "ship-to-state", "ship-to-country", "ship-to-phone",
    ].forEach((id) => { byId(id).value = ""; });
  }

  async function selectClient(clientId, options = {}) {
    clearAlert();
    const previousClient = value("client-id");
    resetContactAndAddress();
    try {
      clientContext = await jsonRequest(`/api/transaction-workspace/clients/${clientId}`);
      const client = clientContext.client;
      byId("client-id").value = client.id;
      byId("selected-client").classList.remove("d-none");
      byId("selected-client").textContent = `${client.code} — ${client.name} — ${client.address}`;
      byId("client-results").replaceChildren();

      const contactSelect = byId("saved-contact");
      clientContext.contacts.forEach((contact) => {
        contactSelect.appendChild(option(
          `${contact.name}${contact.phone ? ` — ${contact.phone}` : ""}`,
          contact.id,
        ));
      });
      contactSelect.disabled = false;

      const addressSelect = byId("saved-address");
      clientContext.shipping_addresses.forEach((address) => {
        addressSelect.appendChild(option(
          `${address.label || "Saved address"} — ${address.address}`,
          address.id,
        ));
      });
      addressSelect.disabled = false;

      if (previousClient && previousClient !== String(client.id) && selected.size) {
        await revalidateSelected();
      }
      await searchStones();
      if (options.stones) {
        options.stones.forEach((stone) => selected.set(stone.id, stone));
        renderSelected();
      }
      dirty = options.markDirty !== false;
    } catch (error) {
      byId("client-id").value = "";
      byId("selected-client").classList.add("d-none");
      alertUser(error.message);
    }
  }

  async function searchClients() {
    clearAlert();
    const query = value("client-search");
    if (!query) return alertUser("Enter a client code or client name.");
    try {
      const body = await jsonRequest(
        `/api/transaction-workspace/clients?q=${encodeURIComponent(query)}`,
      );
      const list = byId("client-results");
      list.replaceChildren();
      if (!body.clients.length) {
        const empty = document.createElement("div");
        empty.className = "text-muted mt-2";
        empty.textContent = "No matching clients were found.";
        list.appendChild(empty);
        return;
      }
      body.clients.forEach((client) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.textContent = `${client.code} — ${client.name} — ${client.address}`;
        button.addEventListener("click", () => selectClient(client.id));
        list.appendChild(button);
      });
    } catch (error) {
      alertUser(error.message);
    }
  }

  function selectSavedContact() {
    const id = Number(byId("saved-contact").value);
    const contact = clientContext?.contacts.find((item) => item.id === id);
    byId("source-contact-id").value = contact ? contact.id : "";
    byId("person").value = contact?.name || "";
    byId("phone").value = contact?.phone || "";
    byId("fax").value = contact?.fax || "";
    dirty = true;
  }

  function selectSavedAddress() {
    const id = Number(byId("saved-address").value);
    const address = clientContext?.shipping_addresses.find((item) => item.id === id);
    byId("source-shipping-address-id").value = address ? address.id : "";
    const mapping = {
      "ship-to-label": "label", "ship-to-manager": "manager",
      "ship-to-store-number": "store_number", "ship-to-address": "address",
      "ship-to-city": "city", "ship-to-state": "state",
      "ship-to-country": "country", "ship-to-phone": "phone",
    };
    Object.entries(mapping).forEach(([idName, key]) => {
      byId(idName).value = address?.[key] || "";
    });
    dirty = true;
  }

  function stoneSearchParameters(exactNumbers = null) {
    const params = new URLSearchParams();
    if (exactNumbers) {
      params.set("stock_numbers", exactNumbers.join(","));
      return params;
    }
    const numbers = normalizeStockNumbers(byId("stock-numbers").value);
    if (numbers.length > 25) throw new Error("A maximum of 25 stock numbers may be searched at once.");
    if (numbers.length) params.set("stock_numbers", numbers.join(","));
    const fields = [
      ["stock-number", "stock_number"], ["lab", "lab"], ["shape", "shape"],
      ["min-weight", "min_weight"], ["max-weight", "max_weight"],
      ["color", "color"], ["clarity", "clarity"], ["cut", "cut"],
      ["polish", "polish"], ["symmetry", "symmetry"], ["fluorescence", "fluorescence"],
    ];
    fields.forEach(([id, name]) => {
      if (value(id)) params.set(name, value(id));
    });
    if (value("min-weight") && value("max-weight")
        && Number(value("min-weight")) > Number(value("max-weight"))) {
      throw new Error("Minimum weight must not exceed maximum weight.");
    }
    return params;
  }

  function addCell(row, content, className = "") {
    const cell = row.insertCell();
    cell.textContent = String(dash(content));
    cell.className = className;
    return cell;
  }

  function renderEligible(stones) {
    const body = byId("eligible-stones");
    body.replaceChildren();
    if (!stones.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 15;
      cell.className = "text-center text-muted py-4";
      cell.textContent = "No eligible Stones matched this search.";
      return;
    }
    stones.forEach((stone) => {
      const row = body.insertRow();
      if (isHold(stone)) row.className = "table-warning";
      const selectCell = row.insertCell();
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input";
      checkbox.checked = selected.has(stone.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.set(stone.id, stone);
        else selected.delete(stone.id);
        dirty = true;
        renderSelected();
      });
      selectCell.appendChild(checkbox);
      addCell(row, stone.stock_number);
      addCell(row, isHold(stone) ? "Reserved for this client" : "AVAILABLE");
      ["report_number", "lab", "shape", "weight", "color", "clarity", "cut", "polish",
        "symmetry", "fluorescence_intensity"].forEach((key) => addCell(row, stone[key]));
      addCell(row, money(stone.price_per_carat));
      addCell(row, money(stone.total_price));
    });
  }

  async function searchStones(exactNumbers = null) {
    clearAlert();
    const clientId = value("client-id");
    if (!clientId) return alertUser("Select a client before searching for Stones.");
    try {
      const params = stoneSearchParameters(exactNumbers);
      const body = await jsonRequest(
        `/api/clients/${clientId}/eligible-stones?${params.toString()}`,
      );
      renderEligible(body.stones);
      return body.stones;
    } catch (error) {
      alertUser(error.message);
      return null;
    }
  }

  async function revalidateSelected() {
    const oldNumbers = [...selected.values()].map((stone) => stone.stock_number);
    const eligible = [];
    for (let index = 0; index < oldNumbers.length; index += 25) {
      const batch = await searchStones(oldNumbers.slice(index, index + 25));
      if (batch === null) return;
      eligible.push(...batch);
    }
    const eligibleIds = new Set(eligible.map((stone) => stone.id));
    const removed = [...selected.values()]
      .filter((stone) => !eligibleIds.has(stone.id))
      .map((stone) => stone.stock_number);
    eligible.forEach((stone) => selected.set(stone.id, stone));
    [...selected.keys()].forEach((id) => {
      if (!eligibleIds.has(id)) selected.delete(id);
    });
    renderSelected();
    if (removed.length) {
      alertUser(
        `Removed Stones that are not eligible for the selected client: ${removed.join(", ")}`,
        "warning",
      );
    }
  }

  function renderSelected() {
    const body = byId("selected-stones");
    body.replaceChildren();
    let availableCount = 0;
    let holdCount = 0;
    let totalWeight = 0;
    let totalValue = 0;
    let missingPrices = 0;
    selected.forEach((stone) => {
      if (isHold(stone)) holdCount += 1;
      else availableCount += 1;
      const weight = Number(stone.weight);
      if (Number.isFinite(weight)) totalWeight += weight;
      const price = Number(stone.total_price);
      if (stone.total_price === null || stone.total_price === "" || !Number.isFinite(price)) {
        missingPrices += 1;
      } else {
        totalValue += price;
      }
      const row = body.insertRow();
      addCell(row, stone.stock_number);
      addCell(row, isHold(stone) ? "Reserved for this client" : "AVAILABLE");
      ["report_number", "lab", "shape", "weight"].forEach((key) => addCell(row, stone[key]));
      addCell(row, money(stone.price_per_carat));
      addCell(row, money(stone.total_price));
      const action = row.insertCell();
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-danger";
      button.textContent = "Remove";
      button.addEventListener("click", () => {
        selected.delete(stone.id);
        dirty = true;
        renderSelected();
      });
      action.appendChild(button);
    });
    byId("selected-count").textContent = selected.size;
    byId("available-count").textContent = availableCount;
    byId("hold-count").textContent = holdCount;
    byId("weight-total").textContent = totalWeight.toFixed(2);
    byId("value-total").textContent = selected.size ? money(totalValue) : "—";
    const note = byId("missing-price-note");
    note.classList.toggle("d-none", !missingPrices);
    note.textContent = missingPrices
      ? `${missingPrices} selected Stone(s) have missing prices and are excluded from the displayed value.`
      : "";
  }

  function payload() {
    return {
      client_id: value("client-id"),
      date: value("transaction-date"),
      terms: value("terms"),
      carrier: value("carrier"),
      shipment_type: value("shipment-type"),
      ship_charge: value("ship-charge"),
      purchase_order_number: value("purchase-order-number"),
      source_contact_id: value("source-contact-id") || null,
      person: value("person"),
      phone: value("phone"),
      fax: value("fax"),
      source_shipping_address_id: value("source-shipping-address-id") || null,
      ship_to_label: value("ship-to-label"),
      ship_to_manager: value("ship-to-manager"),
      ship_to_store_number: value("ship-to-store-number"),
      ship_to_address: value("ship-to-address"),
      ship_to_city: value("ship-to-city"),
      ship_to_state: value("ship-to-state"),
      ship_to_country: value("ship-to-country"),
      ship_to_phone: value("ship-to-phone"),
      stone_ids: [...selected.keys()],
    };
  }

  async function submitTransaction(mode) {
    if (submitting) return;
    clearAlert();
    if (!form.reportValidity()) return;
    if (!value("client-id")) return alertUser("Select a client.");
    if (!selected.size) return alertUser("Select at least one Stone.");
    let endpoint;
    let confirmation;
    if (mode === "draft") {
      endpoint = "/api/transactions/memos";
    } else if (mode === "active") {
      endpoint = "/api/transactions/memos/active";
      confirmation = "Create and activate this Memo? Selected Stones will no longer be available for other transactions.";
    } else {
      endpoint = "/api/transactions/invoices";
      confirmation = "Create this Invoice? Selected Stones will be marked SOLD.";
    }
    if (confirmation && !window.confirm(confirmation)) return;

    submitting = true;
    document.querySelectorAll("#transaction-form button").forEach((button) => {
      button.disabled = true;
    });
    try {
      const result = await jsonRequest(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      dirty = false;
      window.location.assign(result.detail_url);
    } catch (error) {
      alertUser(error.message);
      document.querySelectorAll("#transaction-form button").forEach((button) => {
        button.disabled = false;
      });
      submitting = false;
    }
  }

  document.querySelectorAll(".transaction-type").forEach((button) => {
    button.addEventListener("click", () => setType(button.dataset.transactionType));
  });
  byId("client-search-button").addEventListener("click", searchClients);
  byId("client-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchClients();
    }
  });
  byId("saved-contact").addEventListener("change", selectSavedContact);
  byId("saved-address").addEventListener("change", selectSavedAddress);
  byId("stock-numbers").addEventListener("input", updateStockNumberCount);
  byId("apply-filters").addEventListener("click", () => searchStones());
  byId("clear-filters").addEventListener("click", () => {
    [
      "stock-numbers", "stock-number", "lab", "shape", "min-weight", "max-weight",
      "color", "clarity", "cut", "polish", "symmetry", "fluorescence",
    ].forEach((id) => { byId(id).value = ""; });
    updateStockNumberCount();
    searchStones();
  });
  byId("clear-selected").addEventListener("click", () => {
    selected.clear();
    dirty = true;
    renderSelected();
    document.querySelectorAll("#eligible-stones input[type=checkbox]").forEach((input) => {
      input.checked = false;
    });
  });
  byId("save-draft")?.addEventListener("click", () => submitTransaction("draft"));
  byId("activate-memo")?.addEventListener("click", () => submitTransaction("active"));
  byId("create-invoice")?.addEventListener("click", () => submitTransaction("invoice"));

  const searchIds = new Set([
    "client-search", "stock-numbers", "stock-number", "lab", "shape", "min-weight",
    "max-weight", "color", "clarity", "cut", "polish", "symmetry", "fluorescence",
  ]);
  form.addEventListener("input", (event) => {
    if (!searchIds.has(event.target.id)) dirty = true;
  });
  window.addEventListener("beforeunload", (event) => {
    if (!dirty || submitting) return;
    event.preventDefault();
    event.returnValue = "You have unsaved transaction information. Leave without saving?";
  });

  setType(transactionType, false);
  renderSelected();
  const prefillElement = byId("transaction-prefill");
  if (prefillElement) {
    try {
      const prefill = JSON.parse(prefillElement.textContent);
      selectClient(prefill.client_id, { stones: prefill.stones, markDirty: false });
    } catch (_error) {
      alertUser("The Inventory selection could not be loaded.");
    }
  }
})();
