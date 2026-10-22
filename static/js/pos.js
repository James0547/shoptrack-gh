/* ShopTrack GH — POS screen logic. */
window.posApp = function () {
  return {
    q: "",
    status: "",
    results: [],
    top: [],
    cart: { items: [], lines: 0, subtotal: 0 },
    pay: { open: false, method: "cash", received: 0, busy: false },
    _searchSeq: 0,

    init() {
      this.loadTop();
      this.loadCart();
      var self = this;
      setTimeout(function () {
        var el = document.getElementById("search-input");
        if (el) el.focus();
      }, 100);
    },

    loadTop() {
      var self = this;
      fetch("/api/top-sellers")
        .then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (d) { self.top = Array.isArray(d.results) ? d.results : []; })
        .catch(function () { self.top = []; });
    },

    loadCart() {
      var self = this;
      fetch("/api/cart")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (d) { if (d.cart) self.cart = d.cart; })
        .catch(function () {});
    },

    doSearch() {
      var self = this;
      var q = (this.q || "").trim();
      if (!q) { this.results = []; this.status = ""; return; }
      var seq = ++this._searchSeq;
      this.status = "searching…";
      fetch("/api/search?q=" + encodeURIComponent(q))
        .then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (d) {
          if (seq !== self._searchSeq) return;
          self.results = Array.isArray(d.results) ? d.results : [];
          self.status = self.results.length ? "" : "no matches";
        })
        .catch(function () { self.status = "offline?"; });
    },

    onEnter() {
      var self = this;
      var q = (this.q || "").trim();
      if (!q) return;
      fetch("/api/barcode/" + encodeURIComponent(q))
        .then(function (r) { return r.ok ? r.json() : { found: false }; })
        .then(function (d) {
          if (d.found) {
            self.addToCart(d.product.id);
            self.clearSearch();
          } else if (self.results.length) {
            self.addToCart(self.results[0].id);
            self.clearSearch();
          }
        })
        .catch(function () {});
    },

    addToCart(productId) {
      var self = this;
      fetch("/api/cart/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: productId, qty: 1 }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.d.ok) { self.toastError(res.d.error || "Could not add"); return; }
          self.cart = res.d.cart;
        })
        .catch(function () { self.toastError("Network error"); });
    },

    setQty(productId, qty) {
      var self = this;
      qty = parseInt(qty);
      if (isNaN(qty) || qty < 0) return;
      fetch("/api/cart/set", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: productId, qty: qty }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.d.ok) { self.toastError(res.d.error || "Could not update"); return; }
          self.cart = res.d.cart;
        })
        .catch(function () { self.toastError("Network error"); });
    },

    removeItem(productId) {
      var self = this;
      fetch("/api/cart/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: productId }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) { if (res.ok && res.d.ok) self.cart = res.d.cart; })
        .catch(function () {});
    },

    clearCart() {
      var self = this;
      if (!confirm("Clear the cart?")) return;
      fetch("/api/cart/clear", { method: "POST" })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) { if (res.ok && res.d.ok) self.cart = res.d.cart; })
        .catch(function () {});
    },

    clearSearch() {
      this.q = "";
      this.results = [];
      this.status = "";
      var el = document.getElementById("search-input");
      if (el) el.focus();
    },

    toastError(msg) {
      window.dispatchEvent(new CustomEvent("pos-error", { detail: msg }));
    },

    openPay(method) {
      if (!this.cart.items || !this.cart.items.length) return;
      this.pay.method = method;
      this.pay.received = this.cart.subtotal;
      this.pay.open = true;
      this.pay.busy = false;
    },

    confirmPay() {
      var self = this;
      if (this.pay.busy) return;
      if (!this.cart.items || !this.cart.items.length) { this.toastError("Cart empty"); return; }
      if (this.pay.method === "cash" && this.pay.received < this.cart.subtotal) {
        this.toastError("Amount received is less than total");
        return;
      }
      this.pay.busy = true;
      fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          payment: this.pay.method,
          amount_paid: this.pay.method === "cash" ? this.pay.received : this.cart.subtotal,
        }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.d.ok) {
            self.toastError(res.d.error || "Checkout failed");
            self.pay.busy = false;
            return;
          }
          self.loadCart();
          self.pay.open = false;
          self.pay.busy = false;
          var win = window.open(res.d.url, "_blank");
          if (!win) window.location.href = res.d.url;
        })
        .catch(function () {
          self.toastError("Network error during checkout");
          self.pay.busy = false;
        });
    },
  };
};