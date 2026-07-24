const scanned = [];

const input = document.getElementById("returnBarcodeInput");

if (input) {

    input.addEventListener("keydown", async (e) => {

        if (e.key !== "Enter")
            return;

        e.preventDefault();

        const stockNumber = input.value.trim();

        if (!stockNumber)
            return;

        input.value = "";

        try {

            const response = await fetch(
                `/api/stone-by-stock/${stockNumber}`
            );

            if (!response.ok) {
                alert("Stone not found");
                return;
            }

            const stone = await response.json();

            console.log("SCANNED:", stone);

            if (!stone.id) {
                alert("Invalid stone data returned");
                return;
            }

            if (scanned.includes(stone.id))
                return;

            scanned.push(stone.id);

            document
                .getElementById("returnList")
                .insertAdjacentHTML(
                    "beforeend",
                    `
                    <tr>
                        <td>${stone.stock_number}</td>
                        <td>${stone.status}</td>
                    </tr>
                    `
                );

        } catch (err) {

            console.error(err);
            alert("Scan failed");

        }

    });

}

document
    .getElementById("processReturnBtn")
    ?.addEventListener("click", async () => {

        if (scanned.length === 0) {
            alert("No stones scanned");
            return;
        }

        try {

            const response = await fetch(
                "/receive-stones",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        stone_ids: scanned
                    })
                }
            );

            const result = await response.json();

            if (result.success) {

                alert("Stones received");

                scanned.length = 0;

                document.getElementById("returnList").innerHTML = "";

                bootstrap.Modal
                    .getInstance(
                        document.getElementById("returnModal")
                    )
                    ?.hide();

            } else {

                alert(result.error || "Receive failed");

            }

        } catch (err) {

            console.error(err);
            alert("Receive failed");

        }

    });