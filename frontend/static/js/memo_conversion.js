(() => {
  const root = document.getElementById("memo-conversion");
  if (!root) return;

  const form = document.getElementById("memo-conversion-form");
  const submit = document.getElementById("convert-memo");
  const alertBox = document.getElementById("conversion-alert");
  const items = [...document.querySelectorAll(".conversion-item")];
  let submitting = false;
  let dirty = false;

  const value = (id) => document.getElementById(id).value.trim();
  const selected = () => items.filter((item) => item.checked);

  function showError(message) {
    alertBox.className = "alert alert-danger";
    alertBox.textContent = message;
    alertBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function totals() {
    let weight = 0;
    let amount = 0;
    let priced = 0;
    selected().forEach((item) => {
      const itemWeight = Number(item.dataset.weight);
      const itemTotal = Number(item.dataset.total);
      if (Number.isFinite(itemWeight)) weight += itemWeight;
      if (item.dataset.total !== "" && Number.isFinite(itemTotal)) {
        amount += itemTotal;
        priced += 1;
      }
    });
    document.getElementById("conversion-count").textContent = selected().length;
    document.getElementById("conversion-weight").textContent = weight.toFixed(2);
    document.getElementById("conversion-value").textContent = priced
      ? new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: "USD",
        }).format(amount)
      : "—";
  }

  function payload() {
    return {
      transaction_item_ids: selected().map((item) => Number(item.value)),
      date: value("conversion-date"),
      terms: value("conversion-terms"),
      carrier: value("conversion-carrier"),
      shipment_type: value("conversion-shipment-type"),
      ship_charge: value("conversion-ship-charge"),
      purchase_order_number: value("conversion-po"),
      person: value("conversion-person"),
      phone: value("conversion-phone"),
      fax: value("conversion-fax"),
      ship_to_label: value("conversion-ship-label"),
      ship_to_manager: value("conversion-ship-manager"),
      ship_to_store_number: value("conversion-store-number"),
      ship_to_address: value("conversion-ship-address"),
      ship_to_city: value("conversion-ship-city"),
      ship_to_state: value("conversion-ship-state"),
      ship_to_country: value("conversion-ship-country"),
      ship_to_phone: value("conversion-ship-phone"),
    };
  }

  items.forEach((item) => {
    item.addEventListener("change", () => {
      dirty = true;
      totals();
    });
  });
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    if (!form.reportValidity()) return;
    if (!selected().length) return showError("Select at least one Memo item.");
    if (!window.confirm(
      "The selected Memo items will be invoiced and their Stones will be marked Sold. " +
      "Unselected items will remain on the Memo.",
    )) return;

    submitting = true;
    submit.disabled = true;
    try {
      const response = await dsmsFetch(
        `/api/transactions/memos/${root.dataset.memoId}/convert-to-invoice`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload()),
        },
      );
      let body = {};
      try {
        body = await response.json();
      } catch (_error) {
        body = {};
      }
      if (!response.ok) throw new Error(body.error || "The Memo could not be converted.");
      dirty = false;
      window.location.assign(body.detail_url);
    } catch (error) {
      showError(error.message);
      submitting = false;
      submit.disabled = false;
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!dirty || submitting) return;
    event.preventDefault();
    event.returnValue = "You have unsaved transaction information. Leave without saving?";
  });
  totals();
})();
