const SHAPE_CODES = {
  Round: "R",
  Heart: "H",
  Pear: "PR",
  Marqueis: "M",
  Oval: "O",
  Aser: "A",
  Emerald: "E",
  Cousand: "C",
  Radiant: "RA",
  Princess: "P",
};

function updateStockPreview() {
  const shape = document.getElementById("shape").value;
  const weight = parseFloat(document.getElementById("weight").value);

  const preview = document.getElementById("stockPreview");

  if (!shape || isNaN(weight)) {
    preview.value = "";
    return;
  }

  const shapeCode = SHAPE_CODES[shape] || "X";

  const caratCode = Math.round(weight * 100)
    .toString()
    .padStart(3, "0");

  preview.value = `${caratCode}${shapeCode}XXXX`;
}
document.getElementById("shape").addEventListener("change", updateStockPreview);

document.getElementById("weight").addEventListener("input", updateStockPreview);

async function loadClient() {
  const code = document.getElementById("client_code").value;

  const response = await fetch(`/clients/by-code/${code}`);

  const data = await response.json();

  document.getElementById("client_id").value = data.client.id;
  document.getElementById("client_name").value = data.client.name;

  const holdButton = document.getElementById("holdButton");

  if (holdButton) {
    holdButton.disabled = false;
  }
}

async function showHoldInfo(stoneId) {
  const response = await fetch(`/inventory/hold-info/${stoneId}`);

  if (!response.ok) {
    alert("Unable to load hold information");
    return;
  }

  const data = await response.json();

  document.getElementById("holdClientText").innerText =
    `On hold for ${data.client_name}`;

  const modal = new bootstrap.Modal(document.getElementById("holdModal"));

  modal.show();
}
