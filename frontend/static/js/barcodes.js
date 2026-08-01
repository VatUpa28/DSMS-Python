document.getElementById("selectAllBtn")?.addEventListener("click", () => {
    document.querySelectorAll(".barcode-checkbox")
        .forEach(cb => cb.checked = true);
});

document.getElementById("clearAllBtn")?.addEventListener("click", () => {
    document.querySelectorAll(".barcode-checkbox")
        .forEach(cb => cb.checked = false);
});

document.getElementById("generatePdfBtn")?.addEventListener("click", async (event) => {

    const ids = [...document.querySelectorAll(".barcode-checkbox:checked")]
        .map(cb => cb.value);

    if (ids.length === 0) {
        alert("Select at least one stone");
        return;
    }

    event.currentTarget.disabled = true;
    const printWindow = window.open("", "_blank");

    try {
        const response = await dsmsFetch("/barcodes/pdf", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ stone_ids: ids })
        });

        if (!response.ok) {
            printWindow?.close();
            alert("Unable to generate barcode labels");
            return;
        }

        const pdf = await response.blob();
        const temporaryUrl = URL.createObjectURL(pdf);
        if (printWindow) {
            printWindow.location = temporaryUrl;
        }
        window.setTimeout(() => URL.revokeObjectURL(temporaryUrl), 60000);
    } finally {
        event.currentTarget.disabled = false;
    }
});
