/* PowerAble — QR pairing flow.
 *
 * Turns a decoded QR payload or manually typed credentials into a secure
 * server-side validation, an explicit confirmation, and a single-use
 * registration request. All security decisions are made by Flask; this file
 * only collects intent and shows the result.
 *
 * The pairing code never leaves the page except in the POST body to the
 * local PowerAble API, and it is never written to storage or the URL.
 */

(function () {
  "use strict";

  var page = document.querySelector("[data-pairing-page]");
  if (!page) {
    return;
  }

  var PowerAble = window.PowerAble || {};
  var validateUrl = page.getAttribute("data-validate-url");
  var registerUrl = page.getAttribute("data-register-url");

  var scannerRoot = document.querySelector("[data-scanner]");
  var secureWarning = document.querySelector("[data-secure-warning]");
  var manualForm = document.querySelector("[data-manual-form]");
  var manualError = document.querySelector("[data-manual-error]");

  var confirmSheet = document.querySelector("[data-confirm-sheet]");
  var successSheet = document.querySelector("[data-success-sheet]");
  var confirmDeviceId = document.querySelector("[data-confirm-device-id]");
  var confirmDeviceType = document.querySelector("[data-confirm-device-type]");
  var confirmStatus = document.querySelector("[data-confirm-status]");
  var confirmError = document.querySelector("[data-confirm-error]");
  var registerBtn = document.querySelector("[data-confirm-register]");
  var cancelButtons = document.querySelectorAll("[data-confirm-cancel]");
  var successName = document.querySelector("[data-success-name]");
  var successId = document.querySelector("[data-success-id]");

  var scanner = null;
  var pendingCredentials = null;
  var busy = false;
  var lastFocus = null;

  function showManualError(message) {
    if (!manualError) {
      return;
    }
    manualError.textContent = message || "";
    manualError.hidden = !message;
  }

  function showConfirmError(message) {
    if (!confirmError) {
      return;
    }
    confirmError.textContent = message || "";
    confirmError.hidden = !message;
  }

  function scannerMessage(text) {
    var element = scannerRoot
      ? scannerRoot.querySelector("[data-scanner-message]")
      : null;
    if (element) {
      element.textContent = text;
    }
  }

  function scannerError(message) {
    if (!scannerRoot) {
      return;
    }
    var element = scannerRoot.querySelector("[data-scanner-error]");
    if (element) {
      element.textContent = message || "";
      element.hidden = !message;
    }
  }

  function clearScannerError() {
    scannerError("");
  }

  function secureContextMissing() {
    return window.isSecureContext === false;
  }

  function openSheet(sheet) {
    if (!sheet) {
      return;
    }
    lastFocus = document.activeElement;
    sheet.hidden = false;
    document.body.classList.add("sheet-open");
    var focusTarget = sheet.querySelector(
      "button:not([disabled]), a[href], input:not([disabled])"
    );
    if (focusTarget) {
      window.setTimeout(function () {
        focusTarget.focus();
      }, 30);
    }
  }

  function closeSheet(sheet) {
    if (!sheet) {
      return;
    }
    sheet.hidden = true;

    var anyOpen = false;
    document.querySelectorAll(".pair-sheet").forEach(function (panel) {
      if (!panel.hidden) {
        anyOpen = true;
      }
    });
    if (!anyOpen) {
      document.body.classList.remove("sheet-open");
    }

    if (lastFocus && typeof lastFocus.focus === "function") {
      lastFocus.focus();
    }
  }

  function resumeScannerIfLive() {
    if (scanner && scanner.isRunning && scanner.isRunning()) {
      scanner.resume();
      scannerMessage("Scanning for a PowerAble label…");
    }
  }

  function friendlyApiError(response, data) {
    if (response && response.status === 401) {
      return "Your session expired. Sign in again, then return to this page to pair the outlet.";
    }
    if (data && data.message) {
      return data.message;
    }
    if (response && response.status === 400) {
      return "The request was rejected. Refresh the page and try again.";
    }
    if (response && response.status === 429) {
      return "Too many attempts. Wait ten minutes and try again.";
    }
    return "The local server could not complete that request. Check the terminal for details.";
  }

  function openConfirmation(device) {
    if (confirmDeviceId) {
      confirmDeviceId.textContent = device.device_id || "—";
    }
    if (confirmDeviceType) {
      confirmDeviceType.textContent = device.device_type || "Smart Power Outlet";
    }
    if (confirmStatus) {
      confirmStatus.textContent = device.registration_status || "Available";
    }
    showConfirmError("");
    openSheet(confirmSheet);
  }

  function validateCredentials(credentials, source) {
    if (busy) {
      return;
    }

    if (!PowerAble.apiPost || !validateUrl) {
      var setupMessage =
        "The pairing scripts did not load. Refresh the page and try again.";
      if (source === "manual") {
        showManualError(setupMessage);
      } else {
        scannerError(setupMessage);
      }
      return;
    }

    busy = true;
    clearScannerError();
    showManualError("");

    if (scanner && scanner.pause) {
      scanner.pause();
    }
    scannerMessage("Checking the label with the local server…");

    PowerAble.apiPost(validateUrl, credentials)
      .then(function (result) {
        var response = result.response;
        var data = result.data || {};

        if (response.ok && data.ok && data.device) {
          pendingCredentials = credentials;
          openConfirmation(data.device);
          return;
        }

        var message = friendlyApiError(response, data);
        if (source === "manual") {
          showManualError(message);
        } else {
          scannerError(message);
          scannerMessage("That label was not accepted. Adjust the view or use manual entry.");
          resumeScannerIfLive();
        }
      })
      .catch(function () {
        var message =
          "Could not reach the local PowerAble server. Check that it is still running.";
        if (source === "manual") {
          showManualError(message);
        } else {
          scannerError(message);
          resumeScannerIfLive();
        }
      })
      .then(function () {
        busy = false;
      });
  }

  function registerOutlet() {
    if (busy || !pendingCredentials) {
      return;
    }

    if (!PowerAble.apiPost || !registerUrl) {
      showConfirmError(
        "The pairing scripts did not load. Refresh the page and try again."
      );
      return;
    }

    busy = true;
    showConfirmError("");

    var originalLabel = registerBtn ? registerBtn.innerHTML : "";
    if (registerBtn) {
      registerBtn.disabled = true;
      registerBtn.textContent = "Registering…";
    }

    PowerAble.apiPost(registerUrl, pendingCredentials)
      .then(function (result) {
        var response = result.response;
        var data = result.data || {};

        if (response.ok && data.ok && data.device) {
          if (scanner && scanner.stop) {
            scanner.stop();
          }
          pendingCredentials = null;
          closeSheet(confirmSheet);

          if (successName) {
            successName.textContent = data.device.name || "Smart Outlet";
          }
          if (successId) {
            successId.textContent = data.device.device_id || "";
          }
          if (manualForm) {
            manualForm.reset();
          }
          scannerMessage("Registration complete.");
          openSheet(successSheet);
          return;
        }

        showConfirmError(friendlyApiError(response, data));
      })
      .catch(function () {
        showConfirmError(
          "Could not reach the local PowerAble server. Check that it is still running."
        );
      })
      .then(function () {
        busy = false;
        if (registerBtn) {
          registerBtn.disabled = false;
          registerBtn.innerHTML = originalLabel;
        }
      });
  }

  function cancelConfirmation() {
    if (busy) {
      return;
    }
    pendingCredentials = null;
    showConfirmError("");
    closeSheet(confirmSheet);
    resumeScannerIfLive();
  }

  /* ---------- Scanner setup ---------- */

  if (scannerRoot && typeof PowerAble.createScanner === "function") {
    scanner = PowerAble.createScanner(scannerRoot, {
      onDecode: function (raw) {
        if (busy) {
          return;
        }
        validateCredentials({ payload: raw }, "scan");
      },
      onStateChange: function (state) {
        if (state === "error") {
          scannerMessage(
            "Camera unavailable. Manual entry works without a camera."
          );
        }
      },
    });
  } else if (scannerRoot) {
    scannerError(
      "The scanner engine did not load. You can still enter the device ID and pairing code manually."
    );
  }

  /* ---------- Secure context notice ---------- */

  if (secureWarning && secureContextMissing()) {
    secureWarning.hidden = false;
  }

  /* ---------- Manual entry ---------- */

  if (manualForm) {
    manualForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (busy) {
        return;
      }

      var fields = new FormData(manualForm);
      var deviceId = String(fields.get("device_id") || "").trim();
      var pairingCode = String(fields.get("pairing_code") || "").trim();

      if (!deviceId || !pairingCode) {
        showManualError("Enter both the device ID and the pairing code.");
        return;
      }

      validateCredentials(
        { device_id: deviceId, pairing_code: pairingCode },
        "manual"
      );
    });
  }

  /* ---------- Confirmation sheet ---------- */

  if (registerBtn) {
    registerBtn.addEventListener("click", registerOutlet);
  }

  Array.prototype.forEach.call(cancelButtons, function (button) {
    button.addEventListener("click", cancelConfirmation);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    if (confirmSheet && !confirmSheet.hidden) {
      cancelConfirmation();
    }
  });

  /* A phone that locks the screen should release the camera. */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && scanner && scanner.isRunning && scanner.isRunning()) {
      scanner.pause();
    }
  });

  window.addEventListener("pagehide", function () {
    if (scanner && scanner.isRunning && scanner.isRunning()) {
      scanner.stop();
    }
  });
})();
