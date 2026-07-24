let clientData = null;

/* ===================== CLIENT LOAD ===================== */
async function loadFullClient() {
  const code = document.getElementById("client_code").value.trim();
  if (!code) return alert("Enter client code");

  const res = await fetch(`/clients/by-code/${code}`);
  if (!res.ok) return alert("Client not found");

  const data = await res.json();
  console.log("API RESPONSE:", data);

  // FIX: normalize shape
  const client = data.client || data;

  clientData = data;

  document.getElementById("client_id").value = client.id || "";
  document.getElementById("client_name").value = client.name || "";
  document.getElementById("client_address").value = client.address || "";
  document.getElementById("ship_to_address").value = client.address || "";
}

/* ===================== SHIPPING ===================== */
function toggleShipping() {
  const checked = document.getElementById("alt_ship").checked;

  if (!checked && clientData) {
    document.getElementById("ship_to_address").value =
      clientData.client.address || "";
  }
}

function openShippingModal() {
  if (!clientData) {
    alert("Load client first");
    return;
  }

  const list = document.getElementById("shipping_list");
  list.innerHTML = "";

  const addresses = clientData.shipping_addresses || [];

  if (addresses.length === 0) {
    list.innerHTML = `<tr><td colspan="3">No addresses found</td></tr>`;
  }

  addresses.forEach((a) => {
    const row = document.createElement("tr");

    row.innerHTML = `
        <td>${a.label || ""}</td>
        <td>${a.manager || ""}</td>
        <td>${a.address || ""}</td>
        <td>
          <button type="button" class="btn btn-sm btn-primary">
            Select
          </button>
        </td>
      `;

    row.querySelector("button").addEventListener("click", () => {
      selectShipping(a);
    });

    list.appendChild(row);
  });

  new bootstrap.Modal(document.getElementById("shippingModal")).show();
}

function selectShipping(addr) {
  document.getElementById("ship_to_address").value =
    `${addr.address || ""}, ${addr.city || ""}, ${addr.state || ""}, ${addr.country || ""}`;

  const modalEl = document.getElementById("shippingModal");
  bootstrap.Modal.getInstance(modalEl)?.hide();
}

/* ===================== CONTACT ===================== */
async function openContactModal() {
  const clientId = document.getElementById("client_id").value;

  console.log("clientId:", clientId);

  if (!clientId) {
    alert("Load client first");
    return;
  }

  const res = await fetch(`/api/clients/${clientId}/contacts`);

  if (!res.ok) {
    console.error("Failed to load contacts");
    return;
  }

  const data = await res.json();

  const contacts = data.contacts || [];

  const tbody = document.getElementById("contact_list");
  tbody.innerHTML = "";

  contacts.forEach((c) => {
    const row = document.createElement("tr");

    row.innerHTML = `
        <td>${c.name || ""}</td>
        <td>${c.phone || ""}</td>
        <td>${c.email || ""}</td>
        <td><button type="button" class="btn btn-sm btn-primary">Select</button></td>
      `;

    row.querySelector("button").onclick = () => selectContact(c);

    tbody.appendChild(row);
  });

  bootstrap.Modal.getOrCreateInstance(
    document.getElementById("contactModal"),
  ).show();
}
function selectContact(contact) {
  document.querySelector("input[name='person']").value = contact.name || "";

  const modalEl = document.getElementById("contactModal");
  bootstrap.Modal.getInstance(modalEl)?.hide();
}

async function handleReturn(e) {
  e.preventDefault();

  const form = e.target;
  const res = await fetch(form.action, {
    method: "POST",
    body: new FormData(form),
  });

  if (!res.ok) {
    console.error("Return failed");
    return;
  }

  window.location.reload();
}

function submitAction(action) {
  const form = document.getElementById("memoForm");

  const formData = new FormData(form);

  // DEBUG
  console.log([...formData.entries()]);

  fetch(`/transactions/${TRANSACTION_ID}/create-invoice`, {
    method: "POST",
    body: formData,
  });
}

const memoForm = document.querySelector('form[action="/create-memo"]');

memoForm.addEventListener("submit", (e) => {
  if (document.activeElement.id === "barcodeInput") {
    e.preventDefault();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("memoBarcodeInput");
  if (!input) return;

  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;

    e.preventDefault();

    const scanned = input.value.trim().toUpperCase();
    if (!scanned) return;

    const checkbox =
      document.querySelector(`[data-stock-number="${scanned}"]`) ||
      document.querySelector(`input[name="stone_ids"][value="${scanned}"]`);

    if (!checkbox) {
      console.log("NO MATCH:", scanned);
      input.value = "";
      return;
    }

    checkbox.checked = true;

    checkbox.closest("tr")?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });

    input.value = "";
  });
});