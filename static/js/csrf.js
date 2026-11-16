(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (!meta) return;
  const token = meta.content;
  const originalFetch = window.fetch.bind(window);

  window.fetch = function (input, init = {}) {
    const isRequest = input instanceof Request;
    const method = init.method || (isRequest ? input.method : "GET");
    const url = new URL(isRequest ? input.url : input, location.href);
    if (!/^(GET|HEAD|OPTIONS)$/i.test(method) && url.origin === location.origin) {
      const headers = new Headers(init.headers || (isRequest ? input.headers : undefined));
      headers.set("X-CSRF-Token", token);
      init = { ...init, headers };
    }
    return originalFetch(input, init);
  };

  document.addEventListener("submit", e => {
    const form = e.target;
    if ((form.getAttribute("method") || "").toLowerCase() !== "post" || form.elements.csrf_token) return;
    form.append(Object.assign(document.createElement("input"), { type: "hidden", name: "csrf_token", value: token }));
  }, true);
})();
