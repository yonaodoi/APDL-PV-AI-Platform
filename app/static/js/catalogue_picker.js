// Company product catalogue picker.
// <select data-catalogue-picker data-hidden="name-of-hidden-id-input"
//         data-fill='{"brand_name": "field-name", ...}' data-selected="12">
// Choosing a product fills the reusable facts into EMPTY fields only; a field
// that already holds something different is left as it is and flagged, so
// what the reporter or user entered is never overwritten.
(function () {
    var cache = null, waiting = [];
    function load(src, done) {
        if (cache) return done(cache);
        waiting.push(done);
        if (waiting.length > 1) return;
        fetch(src, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : []; })
            .catch(function () { return []; })
            .then(function (rows) { cache = rows; waiting.forEach(function (fn) { fn(rows); }); waiting = []; });
    }
    function field(form, name) {
        return form.querySelector('[name="' + name + '"]') || document.getElementById(name);
    }
    function setup(select) {
        if (select.dataset.ready) return;
        select.dataset.ready = "1";
        var form = select.form || document;
        load(select.dataset.src || "/products/options.json", function (rows) {
            var want = select.dataset.selected || "";
            select.innerHTML = '<option value="">Not from the catalogue / not listed</option>';
            rows.forEach(function (p) {
                var o = document.createElement("option");
                o.value = p.product_id; o.textContent = p.label;
                if (String(p.product_id) === want) o.selected = true;
                select.appendChild(o);
            });
            if (!rows.length) {
                var o = document.createElement("option"); o.disabled = true; o.textContent = "(The catalogue is empty)"; select.appendChild(o);
            }
        });
        select.addEventListener("change", function () {
            var hidden = select.dataset.hidden ? field(form, select.dataset.hidden) : null;
            if (hidden) hidden.value = select.value;
            var product = (cache || []).find(function (p) { return String(p.product_id) === select.value; });
            var note = select.parentElement.querySelector(".catalogue-note");
            if (!note) { note = document.createElement("p"); note.className = "catalogue-note f-hint"; select.parentElement.appendChild(note); }
            if (!product) { note.textContent = ""; return; }
            var fill = {};
            try { fill = JSON.parse(select.dataset.fill || "{}"); } catch (e) {}
            var filled = [], kept = [];
            Object.keys(fill).forEach(function (key) {
                var target = field(form, fill[key]), value = product[key];
                if (!target || !value) return;
                if (!target.value.trim()) { target.value = value; filled.push(key.replace(/_/g, " ")); target.dispatchEvent(new Event("input", { bubbles: true })); }
                else if (target.value.trim().toLowerCase() !== String(value).toLowerCase()) { kept.push(key.replace(/_/g, " ") + " (catalogue: " + value + ")"); }
            });
            note.textContent = (filled.length ? "Filled from the catalogue: " + filled.join(", ") + ". " : "No empty fields to fill. ")
                + (kept.length ? "Kept what was entered for " + kept.join("; ") + "." : "");
        });
    }
    window.setupCataloguePickers = function (root) { (root || document).querySelectorAll("select[data-catalogue-picker]").forEach(setup); };
    document.addEventListener("DOMContentLoaded", function () { window.setupCataloguePickers(); });
})();
