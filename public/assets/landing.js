// =====================================================================
//  landing.js - moves the shopping cart while you scroll
//
//  How it works:
//   1. Every section ("scene") has a spot where the cart should be:
//      x and y (in % of the screen), rotation (degrees) and size (scale).
//   2. While you scroll from one scene to the next, the cart glides
//      from the first spot to the second one.
//   3. The wheels turn with the scroll, and the products drop into the
//      basket during the third scene.
// =====================================================================
(function () {
    var cart = document.getElementById("cart");
    var scenes = Array.prototype.slice.call(document.querySelectorAll(".scene"));
    var drops = Array.prototype.slice.call(document.querySelectorAll(".drop"));
    var bar = document.getElementById("progress");
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Where the cart sits in each scene (computer screens)
    var desktopSpots = [
        { x: 70, y: 50, rotate: -6,  scale: 1.0  },   // 1 hero
        { x: 28, y: 52, rotate: 8,   scale: 0.92 },   // 2 browse
        { x: 72, y: 52, rotate: -10, scale: 1.0  },   // 3 add to cart
        { x: 28, y: 50, rotate: 5,   scale: 0.85 },   // 4 pay
        { x: 72, y: 54, rotate: -4,  scale: 0.95 },   // 5 track
        { x: 26, y: 52, rotate: 0,   scale: 1.0  }    // 6 log in
    ];
    // Where the cart sits on phones (always in the top half)
    var phoneSpots = [
        { x: 50, y: 27, rotate: -6, scale: 0.95 },
        { x: 50, y: 25, rotate: 6,  scale: 0.85 },
        { x: 50, y: 27, rotate: -8, scale: 0.95 },
        { x: 50, y: 25, rotate: 5,  scale: 0.8  },
        { x: 50, y: 27, rotate: -4, scale: 0.9  },
        { x: 50, y: 22, rotate: 0,  scale: 0.7  }
    ];

    function lerp(a, b, t) { return a + (b - a) * t; }
    function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }
    function ease(t) { return t * t * (3 - 2 * t); }   // smooth start and end

    function update() {
        var viewHeight = window.innerHeight;
        var spots = window.innerWidth < 820 ? phoneSpots : desktopSpots;
        var middle = window.scrollY + viewHeight / 2;      // the middle of the screen

        // Which two scenes are we between?
        var centers = scenes.map(function (s) { return s.offsetTop + s.offsetHeight / 2; });
        var i = 0;
        while (i < centers.length - 1 && middle >= centers[i + 1]) { i++; }
        var t = 0;
        if (i < centers.length - 1 && middle > centers[0]) {
            t = clamp((middle - centers[i]) / (centers[i + 1] - centers[i]), 0, 1);
        }

        // The cart rests for a moment inside each scene, then glides to the next
        var glide = ease(clamp((t - 0.2) / 0.6, 0, 1));
        var from = spots[i];
        var to = spots[Math.min(i + 1, spots.length - 1)];

        if (!reduceMotion) {
            var x = lerp(from.x, to.x, glide);
            var y = lerp(from.y, to.y, glide);
            var rotate = lerp(from.rotate, to.rotate, glide);
            var scale = lerp(from.scale, to.scale, glide);
            cart.style.transform = "translate(" + x + "vw, " + y + "vh) translate(-50%, -50%) rotate(" + rotate + "deg) scale(" + scale + ")";
            cart.style.setProperty("--wheel", (window.scrollY * 0.45) + "deg");
        }

        // Products drop into the basket one by one (scene 2 to scene 3)
        var position = i + glide;
        drops.forEach(function (drop, n) {
            var amount = reduceMotion ? 1 : clamp((position - (1.45 + n * 0.22)) / 0.35, 0, 1);
            drop.style.opacity = amount;
            drop.style.transform = "translateY(" + ((1 - amount) * -260) + "px) rotate(" + ((1 - amount) * (n - 1) * 25) + "deg)";
        });

        // Progress bar at the top
        var total = document.documentElement.scrollHeight - viewHeight;
        bar.style.width = (total > 0 ? (window.scrollY / total) * 100 : 0) + "%";
    }

    // Run update() smoothly, at most once per screen refresh
    var waiting = false;
    function onScroll() {
        if (!waiting) {
            waiting = true;
            window.requestAnimationFrame(function () { update(); waiting = false; });
        }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    update();

    // Text fades in when it comes into view
    var reveals = document.querySelectorAll(".reveal");
    if ("IntersectionObserver" in window) {
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) { entry.target.classList.add("in"); }
            });
        }, { threshold: 0.25 });
        reveals.forEach(function (el) { observer.observe(el); });
    } else {
        reveals.forEach(function (el) { el.classList.add("in"); });
    }

    // Flash messages (for example "You have been logged out") disappear
    document.querySelectorAll(".toast").forEach(function (toast) {
        setTimeout(function () { toast.style.display = "none"; }, 5000);
    });
})();
