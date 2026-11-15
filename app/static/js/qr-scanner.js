/* PowerAble — local QR scanner engine.
 *
 * Uses the real rear camera through getUserMedia and decodes frames entirely
 * in the browser:
 *   1. the native BarcodeDetector API when it supports QR codes, or
 *   2. the vendored jsQR library as a fallback.
 *
 * No frame, image, or video is uploaded. Only the decoded text is handed to
 * the calling page, which sends it to the local Flask pairing endpoint.
 *
 * Camera access requires a secure context: HTTPS or http://localhost.
 */

(function () {
  "use strict";

  var PowerAble = (window.PowerAble = window.PowerAble || {});
  if (typeof PowerAble.createScanner === "function") {
    return;
  }

  var SCAN_INTERVAL_MS = 190;
  var FALLBACK_MAX_WIDTH = 720;
  var DUPLICATE_WINDOW_MS = 2600;
  var MAX_DETECTOR_FAILURES = 4;

  var STATE_LABELS = {
    idle: "Camera off",
    starting: "Starting camera",
    running: "Scanning",
    paused: "Paused",
    error: "Camera error",
  };

  function friendlyCameraError(error) {
    var name = (error && error.name) || "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return "Camera permission was denied. Allow camera access in your browser settings, then try again. Manual entry still works.";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "No camera was found on this device. Use manual entry below.";
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return "The camera is busy in another app. Close that app and try again.";
    }
    if (name === "OverconstrainedError") {
      return "This camera does not support the requested mode. Try the main camera or manual entry.";
    }
    if (name === "SecurityError") {
      return "The browser blocked the camera on this connection. Open PowerAble over HTTPS or localhost.";
    }
    return "The camera could not start. Use manual entry below if the problem continues.";
  }

  /**
   * Attach the scanner behaviour to a scanner shell.
   *
   * options.onDecode(rawText) is called once per distinct code.
   * options.onStateChange(state) is optional.
   */
  PowerAble.createScanner = function (root, options) {
    if (!root) {
      return null;
    }

    var settings = options || {};
    var stage = root.querySelector("[data-camera-stage]");
    var video = root.querySelector("[data-camera-video]");
    var placeholder = root.querySelector("[data-camera-placeholder]");
    var hint = root.querySelector("[data-camera-hint]");
    var startBtn = root.querySelector("[data-start-camera]");
    var stopBtn = root.querySelector("[data-stop-camera]");
    var torchBtn = root.querySelector("[data-torch]");
    var switchBtn = root.querySelector("[data-switch-camera]");
    var stateChip = root.querySelector("[data-camera-state]");
    var messageEl = root.querySelector("[data-scanner-message]");
    var errorEl = root.querySelector("[data-scanner-error]");
    var permissionLabel = document.querySelector("[data-permission-label]");
    var permissionPill = document.querySelector("[data-permission-pill]");

    if (!video || !startBtn) {
      return null;
    }

    var stream = null;
    var track = null;
    var detector = null;
    var detectorFailures = 0;
    var canvas = null;
    var context = null;
    var timer = null;
    var generation = 0;
    var paused = true;
    var torchOn = false;
    var torchSupported = false;
    var cameras = [];
    var lastCode = "";
    var lastCodeAt = 0;
    var currentState = "idle";

    function hasJsQR() {
      return typeof window.jsQR === "function";
    }

    function setMessage(text) {
      if (messageEl && text) {
        messageEl.textContent = text;
      }
    }

    function clearError() {
      if (errorEl) {
        errorEl.textContent = "";
        errorEl.hidden = true;
      }
    }

    function showError(text) {
      if (errorEl) {
        errorEl.textContent = text;
        errorEl.hidden = false;
      }
    }

    function applyState(state) {
      currentState = state;
      var label = STATE_LABELS[state] || "Camera off";

      if (stateChip) {
        stateChip.textContent = label;
        stateChip.classList.toggle("is-running", state === "running");
        stateChip.classList.toggle("is-error", state === "error");
      }

      if (permissionLabel) {
        permissionLabel.textContent =
          state === "running" ? "Camera ready" : label;
      }

      if (permissionPill) {
        permissionPill.classList.remove("is-waiting", "is-online", "is-offline");
        if (state === "running") {
          permissionPill.classList.add("is-online");
        } else if (state === "error") {
          permissionPill.classList.add("is-offline");
        } else {
          permissionPill.classList.add("is-waiting");
        }
      }

      if (stage) {
        stage.classList.toggle("is-running", state === "running");
      }

      if (typeof settings.onStateChange === "function") {
        settings.onStateChange(state);
      }
    }

    function updateControls() {
      var live = Boolean(stream);
      if (startBtn) {
        startBtn.hidden = live;
        startBtn.disabled = window.isSecureContext === false;
      }
      if (stopBtn) {
        stopBtn.hidden = !live;
      }
      if (torchBtn) {
        torchBtn.hidden = !live || !torchSupported;
        torchBtn.setAttribute("aria-pressed", torchOn ? "true" : "false");
      }
      if (switchBtn) {
        switchBtn.hidden = !live || cameras.length < 2;
      }
      if (hint) {
        hint.textContent = live
          ? "Hold the phone steady and keep the whole label visible."
          : "Tap Start camera and allow permission when your browser asks.";
      }
      if (placeholder) {
        placeholder.hidden = live;
      }
    }

    function prepareDetector() {
      if (!("BarcodeDetector" in window)) {
        return Promise.resolve(false);
      }

      var Factory = window.BarcodeDetector;
      var formatsReady = Promise.resolve(["qr_code"]);

      if (typeof Factory.getSupportedFormats === "function") {
        formatsReady = Factory.getSupportedFormats().catch(function () {
          return ["qr_code"];
        });
      }

      return formatsReady
        .then(function (formats) {
          if (Array.isArray(formats) && formats.indexOf("qr_code") === -1) {
            return false;
          }
          detector = new Factory({ formats: ["qr_code"] });
          return true;
        })
        .catch(function () {
          detector = null;
          return false;
        });
    }

    function cameraConstraints(deviceId) {
      if (deviceId) {
        return { video: { deviceId: { exact: deviceId } }, audio: false };
      }
      return {
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      };
    }

    function openCamera(deviceId) {
      return navigator.mediaDevices
        .getUserMedia(cameraConstraints(deviceId))
        .catch(function (error) {
          if (deviceId) {
            return navigator.mediaDevices.getUserMedia({
              video: true,
              audio: false,
            });
          }
          throw error;
        })
        .then(function (mediaStream) {
          stream = mediaStream;
          track = mediaStream.getVideoTracks()[0] || null;
          video.srcObject = mediaStream;
          var played = video.play();
          if (played && typeof played.catch === "function") {
            played.catch(function () {
              /* Autoplay rejection is harmless for a muted inline video. */
            });
          }
          detectTorchSupport();
          return refreshCameras();
        })
        .then(function () {
          updateControls();
          return true;
        })
        .catch(function (error) {
          closeStream();
          showError(friendlyCameraError(error));
          applyState("error");
          updateControls();
          return false;
        });
    }

    function detectTorchSupport() {
      torchSupported = false;
      if (!track || typeof track.getCapabilities !== "function") {
        return;
      }
      try {
        var capabilities = track.getCapabilities();
        torchSupported = Boolean(capabilities && capabilities.torch);
      } catch (error) {
        torchSupported = false;
      }
    }

    function refreshCameras() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        cameras = [];
        return Promise.resolve();
      }
      return navigator.mediaDevices
        .enumerateDevices()
        .then(function (devices) {
          cameras = devices.filter(function (device) {
            return device.kind === "videoinput";
          });
        })
        .catch(function () {
          cameras = [];
        });
    }

    function closeStream() {
      generation += 1;
      paused = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (stream) {
        stream.getTracks().forEach(function (mediaTrack) {
          try {
            mediaTrack.stop();
          } catch (error) {
            /* Already stopped. */
          }
        });
      }
      stream = null;
      track = null;
      torchOn = false;
      torchSupported = false;
      if (video) {
        try {
          video.pause();
        } catch (error) {
          /* Ignore. */
        }
        video.srcObject = null;
      }
    }

    function start() {
      if (stream) {
        resume();
        return Promise.resolve(true);
      }

      clearError();

      if (window.isSecureContext === false) {
        showError(
          "Phones only allow the camera on HTTPS or localhost. Use manual entry below, or open the HTTPS address from the User Manual."
        );
        applyState("error");
        updateControls();
        return Promise.resolve(false);
      }

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showError(
          "This browser cannot access a camera. Use manual entry below."
        );
        applyState("error");
        updateControls();
        return Promise.resolve(false);
      }

      applyState("starting");
      setMessage("Requesting camera permission…");
      updateControls();

      return prepareDetector()
        .then(function () {
          if (!detector && !hasJsQR()) {
            showError(
              "This browser cannot decode QR codes. Use manual entry below."
            );
            applyState("error");
            updateControls();
            return false;
          }
          return openCamera(null);
        })
        .then(function (opened) {
          if (!opened) {
            return false;
          }
          clearError();
          applyState("running");
          setMessage("Scanning for a PowerAble label…");
          startLoop();
          return true;
        });
    }

    function stop() {
      if (!stream) {
        applyState("idle");
        updateControls();
        setMessage("Camera stopped. Tap Start camera to scan again.");
        return;
      }
      closeStream();
      applyState("idle");
      updateControls();
      setMessage("Camera stopped. Tap Start camera to scan again.");
    }

    function pause() {
      paused = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
      if (stream) {
        applyState("paused");
      }
    }

    function resume() {
      if (!stream) {
        return;
      }
      paused = false;
      applyState("running");
      startLoop();
    }

    function torch() {
      if (!track || !torchSupported) {
        return Promise.resolve(false);
      }
      var next = !torchOn;
      return track
        .applyConstraints({ advanced: [{ torch: next }] })
        .then(function () {
          torchOn = next;
          if (torchBtn) {
            torchBtn.setAttribute("aria-pressed", torchOn ? "true" : "false");
          }
          return torchOn;
        })
        .catch(function () {
          showError("The flashlight is not available on this camera.");
          return false;
        });
    }

    function switchCamera() {
      if (!stream || cameras.length < 2) {
        return Promise.resolve(false);
      }

      var currentId =
        (track && track.getSettings && track.getSettings().deviceId) || "";
      var index = -1;
      cameras.forEach(function (camera, position) {
        if (camera.deviceId && camera.deviceId === currentId) {
          index = position;
        }
      });
      var next = cameras[(index + 1) % cameras.length];

      closeStream();
      updateControls();
      applyState("starting");
      setMessage("Switching camera…");

      return openCamera(next.deviceId).then(function (opened) {
        if (opened) {
          clearError();
          applyState("running");
          setMessage("Scanning for a PowerAble label…");
          startLoop();
        }
        return opened;
      });
    }

    function emitDecode(raw) {
      var text = typeof raw === "string" ? raw.trim() : "";
      if (!text) {
        return;
      }

      var now = Date.now();
      if (text === lastCode && now - lastCodeAt < DUPLICATE_WINDOW_MS) {
        return;
      }
      lastCode = text;
      lastCodeAt = now;

      pause();
      setMessage("QR code read. Checking it with the local server…");
      if (typeof settings.onDecode === "function") {
        settings.onDecode(text);
      }
    }

    function decodeWithJsQR() {
      if (!hasJsQR() || !video.videoWidth || !video.videoHeight) {
        return null;
      }

      if (!canvas) {
        canvas = document.createElement("canvas");
        context = canvas.getContext("2d", { willReadFrequently: true });
      }
      if (!context) {
        return null;
      }

      var scale = Math.min(1, FALLBACK_MAX_WIDTH / video.videoWidth);
      var width = Math.max(1, Math.round(video.videoWidth * scale));
      var height = Math.max(1, Math.round(video.videoHeight * scale));

      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }

      context.drawImage(video, 0, 0, width, height);
      var image = context.getImageData(0, 0, width, height);
      var result = window.jsQR(image.data, width, height, {
        inversionAttempts: "dontInvert",
      });
      return result && result.data ? result.data : null;
    }

    function scanOnce(myGeneration) {
      if (myGeneration !== generation || paused || !stream) {
        return;
      }

      var ready = video.readyState >= 2 && video.videoWidth > 0;

      if (!ready) {
        schedule(myGeneration);
        return;
      }

      var work;

      if (detector) {
        work = detector
          .detect(video)
          .then(function (codes) {
            if (codes && codes.length && codes[0].rawValue) {
              emitDecode(codes[0].rawValue);
              return;
            }
            var fallback = decodeWithJsQR();
            if (fallback) {
              emitDecode(fallback);
            }
          })
          .catch(function () {
            detectorFailures += 1;
            if (detectorFailures >= MAX_DETECTOR_FAILURES) {
              detector = null;
            }
            var fallback = decodeWithJsQR();
            if (fallback) {
              emitDecode(fallback);
            }
          });
      } else {
        work = Promise.resolve().then(function () {
          var value = decodeWithJsQR();
          if (value) {
            emitDecode(value);
          }
        });
      }

      work
        .catch(function () {
          /* A failed frame is not a user-facing error. */
        })
        .then(function () {
          schedule(myGeneration);
        });
    }

    function schedule(myGeneration) {
      if (myGeneration !== generation || paused || !stream) {
        return;
      }
      timer = window.setTimeout(function () {
        scanOnce(myGeneration);
      }, SCAN_INTERVAL_MS);
    }

    function startLoop() {
      generation += 1;
      paused = false;
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
      schedule(generation);
    }

    /* ---------- Wiring ---------- */

    startBtn.addEventListener("click", function () {
      start();
    });

    if (stopBtn) {
      stopBtn.addEventListener("click", function () {
        stop();
      });
    }

    if (torchBtn) {
      torchBtn.addEventListener("click", function () {
        torch();
      });
    }

    if (switchBtn) {
      switchBtn.addEventListener("click", function () {
        switchCamera();
      });
    }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden && stream) {
        pause();
      }
    });

    updateControls();
    applyState("idle");

    return {
      start: start,
      stop: stop,
      pause: pause,
      resume: resume,
      torch: torch,
      switchCamera: switchCamera,
      isRunning: function () {
        return Boolean(stream);
      },
      state: function () {
        return currentState;
      },
    };
  };
})();
