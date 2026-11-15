/* PowerAble — shared interface behaviour.
   Provides the CSRF token to other scripts, a small JSON API helper, the
   responsive navigation, and dismissible flash messages.

   No sensor readings or device states are created here. */

(function () {
  "use strict";

  var PowerAble = (window.PowerAble = window.PowerAble || {});

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";

  /**
   * Return the session CSRF token rendered into the page.
   * Pairing requests send it in the X-CSRF-Token header.
   */
  PowerAble.csrfToken = function () {
    return csrfToken;
  };

  /**
   * POST JSON to a local PowerAble endpoint.
   *
   * Resolves with { response, data } even for HTTP errors so callers can
   * show the server's message. Network failures reject.
   */
  PowerAble.apiPost = function (url, payload, options) {
    var settings = options || {};
    var headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    };

    if (settings.headers) {
      Object.keys(settings.headers).forEach(function (key) {
        headers[key] = settings.headers[key];
      });
    }

    return fetch(url, {
      method: "POST",
      headers: headers,
      credentials: "same-origin",
      body: JSON.stringify(payload || {}),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (data) {
          return { response: response, data: data };
        });
    });
  };

  /* ---------- Responsive navigation ---------- */

  var navToggle = document.querySelector("[data-nav-toggle]");
  var navPanel = document.querySelector("[data-nav-panel]");

  function closeMenu() {
    if (!navToggle || !navPanel) {
      return;
    }
    navToggle.setAttribute("aria-expanded", "false");
    navPanel.classList.remove("is-open");
  }

  function openMenu() {
    if (!navToggle || !navPanel) {
      return;
    }
    navToggle.setAttribute("aria-expanded", "true");
    navPanel.classList.add("is-open");
  }

  if (navToggle && navPanel) {
    navToggle.addEventListener("click", function () {
      var isOpen = navToggle.getAttribute("aria-expanded") === "true";
      if (isOpen) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    navPanel.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", closeMenu);
    });

    document.addEventListener("click", function (event) {
      if (!navPanel.classList.contains("is-open")) {
        return;
      }
      if (navPanel.contains(event.target) || navToggle.contains(event.target)) {
        return;
      }
      closeMenu();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeMenu();
      }
    });

    var desktopQuery = window.matchMedia("(min-width: 900px)");
    var handleDesktop = function (event) {
      if (event.matches) {
        closeMenu();
      }
    };

    if (typeof desktopQuery.addEventListener === "function") {
      desktopQuery.addEventListener("change", handleDesktop);
    } else if (typeof desktopQuery.addListener === "function") {
      desktopQuery.addListener(handleDesktop);
    }
  }

  /* ---------- Flash messages ---------- */

  var flashes = document.querySelectorAll(".flash");

  flashes.forEach(function (flash) {
    var dismiss = function () {
      if (flash.classList.contains("is-leaving")) {
        return;
      }
      flash.classList.add("is-leaving");
      window.setTimeout(function () {
        if (flash.parentNode) {
          flash.parentNode.removeChild(flash);
        }
      }, 320);
    };

    flash.addEventListener("click", dismiss);

    if (!flash.classList.contains("flash-error")) {
      window.setTimeout(dismiss, 6500);
    }
  });
})();
