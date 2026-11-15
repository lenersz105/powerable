/* PowerAble — dashboard behaviour.
   Stage 1: real local clock only. No readings, no simulated device activity.
   Stage 4 will add API polling here and feed verified samples into
   window.PowerAble.charts. */

(function () {
  "use strict";

  var clock = document.querySelector("[data-local-clock]");

  function updateClock() {
    if (!clock) {
      return;
    }

    clock.textContent = new Date().toLocaleTimeString([], {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  updateClock();

  if (clock) {
    window.setInterval(updateClock, 1000);
  }
})();
