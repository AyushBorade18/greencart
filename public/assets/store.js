// =====================================================================
//  store.js - small helpers for the shop pages
//  (search bar, mobile menu, filter drawer, quantity buttons)
// =====================================================================
(function () {
    function $(selector) { return document.querySelector(selector); }

    // ---- Search bar under the header ----
    var searchButton = $("#open-search");
    var searchPanel = $("#search-panel");
    if (searchButton && searchPanel) {
        searchButton.addEventListener("click", function () {
            searchPanel.classList.toggle("open");
            if (searchPanel.classList.contains("open")) { $("#search-input").focus(); }
        });
    }

    // ---- Menu button on phones ----
    var menuButton = $("#open-menu");
    var nav = $("#main-nav");
    if (menuButton && nav) {
        menuButton.addEventListener("click", function () { nav.classList.toggle("open"); });
    }

    // ---- Filter drawer on the shop page ----
    var drawer = $("#filter-drawer");
    var backdrop = $("#drawer-backdrop");
    function setDrawer(isOpen) {
        drawer.classList.toggle("open", isOpen);
        backdrop.classList.toggle("open", isOpen);
    }
    if (drawer && backdrop) {
        $("#open-filter").addEventListener("click", function () { setDrawer(true); });
        $("#close-filter").addEventListener("click", function () { setDrawer(false); });
        backdrop.addEventListener("click", function () { setDrawer(false); });
    }

    // ---- Quantity buttons ( - 1 + ) ----
    // In the cart the form saves by itself shortly after the number changes,
    // so the price on the page always matches the quantity.
    function autosave(form) {
        clearTimeout(form._timer);
        form._timer = setTimeout(function () {
            if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
        }, 500);
    }
    document.querySelectorAll(".stepper").forEach(function (box) {
        var input = box.querySelector("input");
        var form = box.closest("form[data-autosubmit]");
        box.querySelectorAll("button").forEach(function (button) {
            button.addEventListener("click", function () {
                var step = Number(button.dataset.step);
                var min = input.min !== "" ? Number(input.min) : 0;
                var max = input.max !== "" ? Number(input.max) : Infinity;
                var before = Number(input.value) || 0;
                var next = Math.min(max, Math.max(min, before + step));
                input.value = next;
                if (form && next !== before) { autosave(form); }
            });
        });
        if (form) {
            input.addEventListener("change", function () { autosave(form); });
        }
    });


    // ---- Product gallery: click a small picture to show it large ----
    var mainPicture = document.getElementById("gallery-main");
    document.querySelectorAll(".gallery-thumbs button").forEach(function (thumb) {
        thumb.addEventListener("click", function () {
            if (mainPicture) { mainPicture.src = thumb.dataset.src; }
            document.querySelectorAll(".gallery-thumbs button").forEach(function (b) { b.classList.remove("on"); });
            thumb.classList.add("on");
        });
    });

    // ---- Messages disappear after a few seconds ----
    document.querySelectorAll(".toast").forEach(function (toast) {
        setTimeout(function () { toast.classList.add("hide"); }, 5000);
    });
})();
