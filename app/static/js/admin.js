/* PowerAble — Admin console interactions.
   Confirmation prompts for destructive forms, copy buttons, and printing.
   All authority is still enforced by the server. */

(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-confirm]");
    if (!form) {
      return;
    }
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  function fallbackCopy(value) {
    var field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    document.body.removeChild(field);
    return copied;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) {
      return;
    }
    var value = button.getAttribute("data-copy") || "";
    var original = button.innerHTML;

    function showResult(ok) {
      button.textContent = ok ? "Copied" : "Copy failed";
      window.setTimeout(function () {
        button.innerHTML = original;
      }, 1600);
    }

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(
        function () {
          showResult(true);
        },
        function () {
          showResult(fallbackCopy(value));
        }
      );
    } else {
      showResult(fallbackCopy(value));
    }
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) {
      window.print();
    }
  });
})();
