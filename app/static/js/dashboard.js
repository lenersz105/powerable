/* PowerAble — dashboard behaviour.
   Handles:
   - Local clock display
   - Relay ON/OFF controls for 3 sockets (waits for hardware acknowledgment)
   - Live status polling
   - Energy readings display */

(function () {
  "use strict";

  var API_BASE = "/api";
  var POLL_INTERVAL = 15000; // 15 seconds
  var ACK_POLL_INTERVAL = 2000; // 2 seconds for command acknowledgment
  var ACK_TIMEOUT_SECONDS = 30; // Max time to wait for acknowledgment
  var csrfToken = document.querySelector('meta[name="csrf-token"]');

  function getCsrfToken() {
    if (csrfToken) return csrfToken.getAttribute("content");
    // Fallback: try to get from hidden form field
    var field = document.querySelector('input[name="csrf_token"]');
    return field ? field.value : "";
  }

  // ---- Local clock ----

  var clock = document.querySelector("[data-local-clock]");

  function updateClock() {
    if (!clock) return;
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

  // ---- Relay control with acknowledgment waiting ----

  function handleRelayToggle(event) {
    var button = event.currentTarget;
    if (button.disabled) return;

    var card = button.closest("[data-device-id]");
    if (!card) return;

    var deviceId = card.getAttribute("data-device-id");
    var socketNumber = parseInt(button.getAttribute("data-socket"), 10);

    // Determine desired state: toggle current state
    var currentStateEl = card.querySelector('[data-relay-state-socket="' + socketNumber + '"]');
    var currentState = currentStateEl ? currentStateEl.textContent.trim().toUpperCase() : "UNKNOWN";
    var desiredState = (currentState === "ON") ? "off" : "on";

    // Disable button during request
    button.disabled = true;
    var originalText = button.textContent;
    button.textContent = "...";

    fetch(API_BASE + "/device/" + encodeURIComponent(deviceId) + "/relay", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": getCsrfToken(),
      },
      body: JSON.stringify({
        socket_number: socketNumber,
        desired_state: desiredState,
      }),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && result.data.ok) {
          // Show "waiting for confirmation" state
          setSocketPending(card, socketNumber);
          showNotification("Command sent for socket " + socketNumber + " — waiting for confirmation", "info");

          // Poll for acknowledgment
          var commandId = result.data.command && result.data.command.command_id;
          pollForAcknowledgment(card, socketNumber, desiredState, commandId, deviceId);
        } else {
          var msg = (result.data && result.data.message) || "Command failed";
          showNotification(msg, "error");
          button.disabled = false;
          button.textContent = originalText;
        }
      })
      .catch(function (error) {
        showNotification("Network error: " + error.message, "error");
        button.disabled = false;
        button.textContent = originalText;
      });
  }

  function setSocketPending(card, socketNumber) {
    var stateEl = card.querySelector('[data-relay-state-socket="' + socketNumber + '"]');
    if (stateEl) {
      stateEl.textContent = "WAITING";
      stateEl.className = "relay-state is-unknown";
      stateEl.style.fontStyle = "italic";
    }
  }

  function pollForAcknowledgment(card, socketNumber, desiredState, commandId, deviceId) {
    var startTime = Date.now();
    var button = card.querySelector('[data-relay-toggle][data-socket="' + socketNumber + '"]');

    function check() {
      var elapsed = (Date.now() - startTime) / 1000;
      if (elapsed > ACK_TIMEOUT_SECONDS) {
        // Timeout — restore button
        if (button) {
          button.disabled = false;
          button.textContent = (desiredState === "on") ? "OFF" : "ON";
        }
        showNotification("Socket " + socketNumber + " command timed out. Device may be offline.", "warning");
        return;
      }

      // The status poll will update the UI when hardware confirms
      // We just keep the button disabled until the next status poll
      setTimeout(check, ACK_POLL_INTERVAL);
    }

    // Start checking — the main status poll will update the UI
    setTimeout(check, ACK_POLL_INTERVAL);

    // Force an early status poll to get the updated state sooner
    setTimeout(function () {
      fetch(API_BASE + "/devices/" + encodeURIComponent(deviceId))
        .then(function (response) {
          if (!response.ok) return null;
          return response.json();
        })
        .then(function (data) {
          if (!data || !data.ok || !data.device) return;
          updateCardFromDevice(card, data.device);
        })
        .catch(function () {});
    }, ACK_POLL_INTERVAL);
  }

  function updateSocketUI(card, socketNumber, state) {
    var stateEl = card.querySelector('[data-relay-state-socket="' + socketNumber + '"]');
    var button = card.querySelector('[data-relay-toggle][data-socket="' + socketNumber + '"]');

    if (stateEl) {
      stateEl.textContent = state.toUpperCase();
      stateEl.className = "relay-state is-" + state;
      stateEl.style.fontStyle = "";
    }
    if (button) {
      button.disabled = false;
      button.textContent = (state === "on") ? "OFF" : "ON";
      button.className = "btn btn-sm btn-relay " + ((state === "on") ? "btn-relay-on" : "btn-relay-off");
    }
  }

  // ---- Status polling ----

  function pollDeviceStatus() {
    var cards = document.querySelectorAll("[data-device-id]");
    cards.forEach(function (card) {
      var deviceId = card.getAttribute("data-device-id");
      fetch(API_BASE + "/devices/" + encodeURIComponent(deviceId))
        .then(function (response) {
          if (!response.ok) return null;
          return response.json();
        })
        .then(function (data) {
          if (!data || !data.ok || !data.device) return;
          updateCardFromDevice(card, data.device);
        })
        .catch(function () {
          // Silently fail on poll errors
        });
    });
  }

  function updateCardFromDevice(card, device) {
    if (!device.sockets) return;

    // Update each socket
    Object.keys(device.sockets).forEach(function (socketNum) {
      var socket = device.sockets[socketNum];
      updateSocketUI(card, parseInt(socketNum), socket.relay_state);
    });

    // Update online status
    var connectionDot = card.querySelector(".monitor-foot .dot");
    var connectionLabel = card.querySelector(".monitor-foot");
    if (connectionLabel) {
      var isOnline = device.online;
      var buttons = card.querySelectorAll("[data-relay-toggle]");
      buttons.forEach(function (btn) {
        if (btn.textContent === "...") return; // Don't interrupt pending commands
        btn.disabled = !isOnline;
        if (!isOnline) {
          btn.title = "Outlet is offline";
        }
      });
    }
  }

  // ---- Notifications ----

  function showNotification(message, type) {
    // Create a temporary notification element
    var notice = document.createElement("div");
    notice.className = "notice notice-" + (type === "success" ? "success" : (type === "info" ? "info" : "warning"));
    var bgColor = type === "success" ? "#d4edda" : (type === "info" ? "#d1ecf1" : "#fff3cd");
    var borderColor = type === "success" ? "#c3e6cb" : (type === "info" ? "#bee5eb" : "#ffeeba");
    var textColor = type === "success" ? "#155724" : (type === "info" ? "#0c5460" : "#856404");
    notice.style.cssText = "position:fixed;top:20px;right:20px;z-index:9999;padding:12px 20px;border-radius:8px;max-width:400px;box-shadow:0 4px 12px rgba(0,0,0,0.15);background:" + bgColor + ";border:1px solid " + borderColor + ";color:" + textColor + ";";
    notice.textContent = message;
    document.body.appendChild(notice);

    setTimeout(function () {
      notice.remove();
    }, 4000);
  }

  // ---- Event delegation for relay buttons ----

  document.addEventListener("click", function (event) {
    if (event.target.matches("[data-relay-toggle]")) {
      handleRelayToggle(event);
    }
  });

  // ---- Start polling ----

  if (document.querySelector("[data-device-id]")) {
    setInterval(pollDeviceStatus, POLL_INTERVAL);
  }
})();
