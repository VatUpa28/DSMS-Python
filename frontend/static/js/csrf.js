function dsmsCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || "";
}

function dsmsFetch(resource, options = {}) {
  const requestOptions = { ...options };
  const method = (requestOptions.method || "GET").toUpperCase();

  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const headers = new Headers(requestOptions.headers || {});
    headers.set("X-CSRFToken", dsmsCsrfToken());
    requestOptions.headers = headers;
  }

  return fetch(resource, requestOptions);
}
