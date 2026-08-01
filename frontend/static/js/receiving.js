(() => {
  const root = document.getElementById("receiving-workspace");
  if (!root) return;

  const form = document.getElementById("receiving-form");
  const alertBox = document.getElementById("receiving-alert");
  const scan = document.getElementById("receiving-scan");
  const bulk = document.getElementById("receiving-bulk");
  const submit = document.getElementById("receive-selected");
  let submitting = false;

  const rows = () => [...document.querySelectorAll("#receiving-rows tr[data-stone-id]")];
  const selectedInputs = () => [...document.querySelectorAll(".receiving-item:checked")];

  function showAlert(message, style = "danger") {
    alertBox.className = `alert alert-${style}`;
    alertBox.textContent = message;
  }

  function normalize(raw) {
    const seen = new Set();
    const values = [];
    raw.split(/[\s,]+/).forEach((part) => {
      const stock = part.trim();
      if (stock && !seen.has(stock)) {
        seen.add(stock);
        values.push(stock);
      }
    });
    return values;
  }

  function updateCount() {
    document.getElementById("receiving-count").textContent = selectedInputs().length;
  }

  function updateBulkCount() {
    document.getElementById("receiving-bulk-count").textContent = normalize(bulk.value).length;
  }

  async function loadJson(url, options = {}) {
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

  async function addExactStock() {
    const stockNumber = scan.value.trim();
    if (!stockNumber) return showAlert("Enter or scan an exact stock number.");
    try {
      const stone = await loadJson(
        `/api/receiving/stone-by-stock?stock_number=${encodeURIComponent(stockNumber)}`,
      );
      const matching = rows().find(
        (row) => row.dataset.stoneId === String(stone.id)
          && row.dataset.stockNumber === stone.stock_number,
      );
      if (!matching) throw new Error("The Stone is awaiting receipt but is not in the current queue view.");
      matching.querySelector(".receiving-item").checked = true;
      matching.scrollIntoView({ behavior: "smooth", block: "center" });
      scan.value = "";
      showAlert(`${stone.stock_number} added to the receiving selection.`, "success");
      updateCount();
    } catch (error) {
      showAlert(error.message);
    }
  }

  function selectBulk() {
    const stockNumbers = normalize(bulk.value);
    if (stockNumbers.length > 100) {
      return showAlert("A maximum of 100 stock numbers may be selected at once.");
    }
    const rowByStock = new Map(rows().map((row) => [row.dataset.stockNumber, row]));
    const missing = [];
    stockNumbers.forEach((stockNumber) => {
      const row = rowByStock.get(stockNumber);
      if (row) row.querySelector(".receiving-item").checked = true;
      else missing.push(stockNumber);
    });
    updateCount();
    if (missing.length) {
      showAlert(
        `Not awaiting receipt or not found in this queue: ${missing.join(", ")}`,
        "warning",
      );
    } else if (stockNumbers.length) {
      showAlert(`${stockNumbers.length} matching Stone(s) selected.`, "success");
    }
  }

  document.querySelectorAll(".receiving-item").forEach((item) => {
    item.addEventListener("change", updateCount);
  });
  bulk.addEventListener("input", updateBulkCount);
  document.getElementById("add-scan").addEventListener("click", addExactStock);
  scan.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addExactStock();
    }
  });
  document.getElementById("select-bulk").addEventListener("click", selectBulk);
  document.getElementById("clear-receiving-search").addEventListener("click", () => {
    scan.value = "";
    bulk.value = "";
    updateBulkCount();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    const selected = selectedInputs();
    if (!selected.length) return showAlert("Select at least one Stone to receive.");
    if (!window.confirm("Confirm physical receipt of the selected Stones?")) return;

    submitting = true;
    submit.disabled = true;
    try {
      const result = await loadJson("/receive-stones", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stone_ids: selected.map((item) => Number(item.value)),
          note: document.getElementById("receiving-note").value,
        }),
      });
      selected.forEach((item) => item.closest("tr").remove());
      document.getElementById("receiving-note").value = "";
      updateCount();
      showAlert(
        `Received ${result.received} Stone(s): ${result.stock_numbers.join(", ")}`,
        "success",
      );
    } catch (error) {
      showAlert(error.message);
    } finally {
      submitting = false;
      submit.disabled = false;
    }
  });

  updateCount();
  updateBulkCount();
})();
