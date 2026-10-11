/* Completion alerts: a short sound, a pop-up and (if allowed) a desktop
   notification when background work finishes, a long form finishes, or a
   file download is ready. Settings are kept in this browser only. */
(function () {
    "use strict";
    var cfg = document.getElementById("pv-alerts");
    if (!cfg) return;
    var pollUrl = cfg.dataset.pollUrl;
    var store = {
        get: function (k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
        set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    };

    // ---- sound -----------------------------------------------------------
    var ctx = null;
    function audio() {
        if (!ctx) {
            var A = window.AudioContext || window.webkitAudioContext;
            if (!A) return null;
            try { ctx = new A(); } catch (e) { return null; }
        }
        if (ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
        return ctx;
    }
    // Browsers only allow sound after the user has clicked or typed on the
    // page, so get ready on the first interaction.
    ["pointerdown", "keydown"].forEach(function (type) {
        document.addEventListener(type, function () { audio(); }, { once: true, capture: true });
    });
    function beep(tone) {
        if (store.get("pv-sound", "on") !== "on") return;
        var c = audio();
        if (!c) return;
        var notes = tone === "error" ? [[440, 0], [330, 0.22]] : [[880, 0], [1175, 0.16], [1568, 0.32]];
        notes.forEach(function (n) {
            var o = c.createOscillator(), g = c.createGain(), t = c.currentTime + n[1];
            o.type = "sine"; o.frequency.value = n[0];
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
            o.connect(g); g.connect(c.destination); o.start(t); o.stop(t + 0.3);
        });
    }

    // ---- pop-up ----------------------------------------------------------
    var box = null;
    function toast(item) {
        if (!box) {
            box = document.createElement("div");
            box.className = "pv-toasts";
            box.setAttribute("aria-live", "polite");
            document.body.appendChild(box);
        }
        var el = document.createElement("div");
        el.className = "pv-toast tone-" + (item.tone || "success");
        el.setAttribute("role", item.tone === "error" ? "alert" : "status");
        var title = document.createElement("strong"); title.textContent = item.title; el.appendChild(title);
        if (item.message) { var p = document.createElement("p"); p.textContent = item.message; el.appendChild(p); }
        var actions = document.createElement("div"); actions.className = "pv-toast-actions";
        if (item.link) { var a = document.createElement("a"); a.href = item.link; a.textContent = "Open"; a.className = "pv-toast-open"; actions.appendChild(a); }
        var x = document.createElement("button"); x.type = "button"; x.textContent = "Dismiss"; x.className = "pv-toast-close";
        x.addEventListener("click", function () { el.remove(); });
        actions.appendChild(x); el.appendChild(actions);
        box.appendChild(el);
        setTimeout(function () { el.classList.add("fade"); setTimeout(function () { el.remove(); }, 600); },
                   item.sticky ? 120000 : 20000);
    }
    function desktop(item) {
        if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
        try {
            var n = new Notification(item.title, { body: item.message || "", tag: "pv-" + (item.id || Date.now()) });
            n.onclick = function () { window.focus(); if (item.link) location.href = item.link; n.close(); };
        } catch (e) {}
    }
    function alertUser(item) { beep(item.tone); toast(item); desktop(item); }
    window.pvAlert = alertUser;

    // ---- background work (AI assessment, document reading) ----------------
    var busyPolling = false;
    function poll() {
        if (busyPolling || !pollUrl) return schedule();
        busyPolling = true;
        fetch(pollUrl, { credentials: "same-origin", headers: { "Accept": "application/json", "X-Requested-With": "fetch" } })
            .then(function (r) { return r.ok ? r.json() : { items: [] }; })
            .then(function (data) {
                (data.items || []).forEach(function (n) {
                    alertUser({ id: n.notification_id, title: n.title, message: n.message, link: n.link, tone: n.tone, sticky: true });
                });
            })
            .catch(function () {})
            .then(function () { busyPolling = false; schedule(); });
    }
    var timer = null;
    function schedule() { clearTimeout(timer); timer = setTimeout(poll, document.hidden ? 45000 : 15000); }
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
    setTimeout(poll, 1500);

    // ---- long forms (marked data-busy) -----------------------------------
    document.addEventListener("submit", function (e) {
        var f = e.target;
        if (f.dataset && f.dataset.busy && !e.defaultPrevented) {
            try { sessionStorage.setItem("pv-busy", f.dataset.busy.replace(/…$/, "")); } catch (err) {}
        }
        watchDownload();
    });
    try {
        var finished = sessionStorage.getItem("pv-busy");
        if (finished) {
            sessionStorage.removeItem("pv-busy");
            var failed = document.querySelector(".flash.flash-error, .error-card, .form-problems, .ui-note.bad[role=alert]");
            alertUser({ title: failed ? "Finished with a problem" : "Finished",
                        message: finished + (failed ? ": see the message on the page." : " is complete."),
                        tone: failed ? "error" : "success" });
        }
    } catch (err) {}

    // ---- file downloads --------------------------------------------------
    // The server sets the pv_download cookie with each file it sends.
    function cookie() { var m = document.cookie.match(/(?:^|; )pv_download=([^;]*)/); return m ? m[1] : ""; }
    var watching = null;
    function watchDownload() {
        var before = cookie(), until = Date.now() + 180000;
        clearInterval(watching);
        watching = setInterval(function () {
            var now = cookie();
            if (now && now !== before) {
                clearInterval(watching);
                if (window.hideBusy) window.hideBusy();
                alertUser({ title: "Download ready", message: "The file has been downloaded. Check your Downloads folder.", tone: "success" });
            } else if (Date.now() > until) {
                clearInterval(watching);
            }
        }, 500);
    }
    document.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("a[href]");
        if (a && !a.target && a.origin === location.origin) watchDownload();
    }, true);

    // ---- settings in the account menu ------------------------------------
    var soundBtn = document.getElementById("pv-sound-toggle");
    function soundLabel() { soundBtn.textContent = "Alert sound: " + (store.get("pv-sound", "on") === "on" ? "on" : "off"); }
    if (soundBtn) {
        soundLabel();
        soundBtn.addEventListener("click", function () {
            store.set("pv-sound", store.get("pv-sound", "on") === "on" ? "off" : "on");
            soundLabel(); beep("success");
        });
    }
    var deskBtn = document.getElementById("pv-desktop-toggle");
    if (deskBtn) {
        if (!("Notification" in window)) deskBtn.hidden = true;
        else {
            var label = function () {
                deskBtn.textContent = Notification.permission === "granted" ? "Desktop alerts: on"
                    : Notification.permission === "denied" ? "Desktop alerts: blocked in the browser" : "Turn on desktop alerts";
            };
            label();
            deskBtn.addEventListener("click", function () {
                if (Notification.permission === "default") Notification.requestPermission().then(label);
            });
        }
    }
})();
