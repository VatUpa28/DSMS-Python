(() => {
  const form = document.getElementById("return-items-form");
  if (!form) return;

  const checkboxes = [...document.querySelectorAll(".return-item")];
  const count = document.getElementById("return-count");
  const weight = document.getElementById("return-weight");
  const value = document.getElementById("return-value");
  const submit = document.getElementById("confirm-return");

  function updateTotals() {
    const checked = checkboxes.filter((item) => item.checked);
    let totalWeight = 0;
    let totalValue = 0;
    let priced = 0;
    checked.forEach((item) => {
      const itemWeight = Number(item.dataset.weight);
      const itemValue = Number(item.dataset.total);
      if (Number.isFinite(itemWeight)) totalWeight += itemWeight;
      if (item.dataset.total !== "" && Number.isFinite(itemValue)) {
        totalValue += itemValue;
        priced += 1;
      }
    });
    count.textContent = checked.length;
    weight.textContent = totalWeight.toFixed(2);
    value.textContent = checked.length && priced
      ? new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: "USD",
        }).format(totalValue)
      : "—";
  }

  checkboxes.forEach((item) => item.addEventListener("change", updateTotals));
  form.addEventListener("submit", (event) => {
    if (!checkboxes.some((item) => item.checked)) {
      event.preventDefault();
      window.alert("Select at least one item to return.");
      return;
    }
    if (!window.confirm(
      "The selected Stones will be marked as awaiting physical receipt. " +
      "They will not become available until Receiving checks them in.",
    )) {
      event.preventDefault();
      return;
    }
    submit.disabled = true;
  });
  updateTotals();
})();
