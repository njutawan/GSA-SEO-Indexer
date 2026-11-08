/* ==========================================================================
   Rara Travel & Tour — main.js
   Progressive enhancement only: page works fully without JavaScript.
   Vanilla ES2018+, deferred, zero dependencies.
   ========================================================================== */
(function () {
  "use strict";

  var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------------------------------------------------------------- Header */
  var header = document.getElementById("site-header");
  var nav = document.getElementById("primary-nav");
  var navToggle = document.getElementById("nav-toggle");
  var desktopQuery = window.matchMedia("(min-width: 900px)");
  var lastScrollY = window.scrollY;
  var scrollTicking = false;

  function closeNav() {
    if (!nav || !navToggle) return;
    nav.classList.remove("is-open");
    navToggle.setAttribute("aria-expanded", "false");
  }

  function onScroll() {
    if (!header) return;
    var y = window.scrollY;
    header.classList.toggle("is-solid", y > 24);
    lastScrollY = y;
    scrollTicking = false;
  }

  window.addEventListener(
    "scroll",
    function () {
      if (!scrollTicking) {
        scrollTicking = true;
        window.requestAnimationFrame(onScroll);
      }
    },
    { passive: true }
  );
  onScroll();

  if (nav && navToggle) {
    navToggle.addEventListener("click", function () {
      var isOpen = navToggle.getAttribute("aria-expanded") === "true";
      navToggle.setAttribute("aria-expanded", String(!isOpen));
      nav.classList.toggle("is-open", !isOpen);
    });

    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) closeNav();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && nav.classList.contains("is-open")) {
        closeNav();
        navToggle.focus();
      }
    });

    document.addEventListener("click", function (event) {
      if (!nav.classList.contains("is-open")) return;
      if (nav.contains(event.target) || navToggle.contains(event.target)) return;
      closeNav();
    });

    var syncNavMode = function () {
      if (desktopQuery.matches) closeNav();
    };
    if (typeof desktopQuery.addEventListener === "function") {
      desktopQuery.addEventListener("change", syncNavMode);
    } else if (typeof desktopQuery.addListener === "function") {
      desktopQuery.addListener(syncNavMode);
    }
  }

  /* ------------------------------------------------- Reveal on scroll (IO) */
  var revealItems = document.querySelectorAll(".reveal");

  if (!("IntersectionObserver" in window) || prefersReducedMotion.matches) {
    revealItems.forEach(function (el) {
      el.classList.add("is-visible");
    });
  } else {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );

    /* Stagger siblings inside the same grid for a softer, premium entrance. */
    document
      .querySelectorAll(".cards, .features, .steps, .routes, .pricing, .testimonials")
      .forEach(function (group) {
        Array.prototype.forEach.call(group.children, function (child, index) {
          child.style.setProperty("--reveal-delay", String(index % 6));
        });
      });

    revealItems.forEach(function (el) {
      observer.observe(el);
    });
  }

  /* ------------------------------------------------------- Scroll spy / nav */
  var navLinks = Array.prototype.slice.call(
    document.querySelectorAll('.nav__link[href^="#"]')
  );
  var sections = navLinks
    .map(function (link) {
      var id = link.getAttribute("href").slice(1);
      return { link: link, section: document.getElementById(id) };
    })
    .filter(function (item) {
      return item.section;
    });

  if (sections.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          sections.forEach(function (item) {
            item.link.classList.toggle("is-active", item.section === entry.target);
          });
        });
      },
      { rootMargin: "-45% 0px -50% 0px", threshold: 0 }
    );
    sections.forEach(function (item) {
      spy.observe(item.section);
    });
  }

  /* ------------------------------------------------- Testimonial controls */
  var track = document.getElementById("testimonials");
  var prevBtn = document.getElementById("testi-prev");
  var nextBtn = document.getElementById("testi-next");

  if (track && prevBtn && nextBtn) {
    var step = function () {
      var first = track.querySelector(".quote");
      if (!first) return track.clientWidth;
      var styles = window.getComputedStyle(track);
      var gap = parseFloat(styles.columnGap || styles.gap) || 0;
      return first.getBoundingClientRect().width + gap;
    };

    var updateButtons = function () {
      var maxScroll = track.scrollWidth - track.clientWidth - 2;
      prevBtn.disabled = track.scrollLeft <= 2;
      nextBtn.disabled = track.scrollLeft >= maxScroll;
    };

    var scrollByStep = function (direction) {
      track.scrollBy({
        left: direction * step(),
        behavior: prefersReducedMotion.matches ? "auto" : "smooth"
      });
    };

    prevBtn.addEventListener("click", function () {
      scrollByStep(-1);
    });
    nextBtn.addEventListener("click", function () {
      scrollByStep(1);
    });

    track.addEventListener("scroll", updateButtons, { passive: true });
    window.addEventListener("resize", updateButtons, { passive: true });
    updateButtons();

    track.addEventListener("keydown", function (event) {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        scrollByStep(1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        scrollByStep(-1);
      }
    });
  }

  /* --------------------------------------------------------- Footer year */
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());
})();
