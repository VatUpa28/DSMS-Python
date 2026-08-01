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
document.getElementById("shape")?.addEventListener("change", updateStockPreview);
document.getElementById("weight")?.addEventListener("input", updateStockPreview);
