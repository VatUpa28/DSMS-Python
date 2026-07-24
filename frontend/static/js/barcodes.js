document.getElementById("selectAllBtn").onclick = () => {
    document.querySelectorAll(".barcode-checkbox")
        .forEach(cb => cb.checked = true);
};

document.getElementById("clearAllBtn").onclick = () => {
    document.querySelectorAll(".barcode-checkbox")
        .forEach(cb => cb.checked = false);
};

document.getElementById("generatePdfBtn").onclick = () => {

    const ids = [...document.querySelectorAll(".barcode-checkbox:checked")]
        .map(cb => cb.value);

    if (ids.length === 0) {
        alert("Select at least one stone");
        return;
    }

    window.location =
        `/barcodes/pdf?stone_ids=${ids.join(",")}`;
};