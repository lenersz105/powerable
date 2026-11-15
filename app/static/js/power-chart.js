/* PowerAble — power trend chart.
   Draws only real samples supplied by Stage 4.
   With no samples it shows a zero baseline and an empty-state message.
   This file never generates random or placeholder measurements. */

(function () {
  "use strict";

  var MAX_SAMPLES = 20;
  var GRID_COLOR = "rgba(148, 163, 184, 0.14)";
  var AXIS_COLOR = "rgba(148, 163, 184, 0.32)";
  var LINE_COLOR = "#22d3ee";
  var FILL_TOP = "rgba(34, 211, 238, 0.22)";
  var FILL_BOTTOM = "rgba(34, 211, 238, 0.01)";
  var LABEL_COLOR = "#70839f";
  var charts = new Map();

  function toSample(value) {
    if (typeof value === "number") {
      return value;
    }
    if (value && typeof value === "object" && "value" in value) {
      return Number(value.value);
    }
    return NaN;
  }

  function PowerChart(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.samples = [];
    this.emptyEl = canvas.parentElement.querySelector("[data-chart-empty]");
    this.draw = this.draw.bind(this);

    window.addEventListener("resize", this.draw);

    if (typeof ResizeObserver !== "undefined") {
      this.observer = new ResizeObserver(this.draw);
      this.observer.observe(canvas);
    }

    this.draw();
  }

  PowerChart.prototype.setSamples = function (samples) {
    if (!Array.isArray(samples)) {
      return;
    }

    this.samples = samples
      .map(toSample)
      .filter(function (value) {
        return Number.isFinite(value) && value >= 0;
      })
      .slice(-MAX_SAMPLES);

    this.draw();
  };

  PowerChart.prototype.clear = function () {
    this.samples = [];
    this.draw();
  };

  PowerChart.prototype.draw = function () {
    var canvas = this.canvas;
    var ctx = this.ctx;
    var rect = canvas.getBoundingClientRect();

    if (!rect.width || !rect.height) {
      return;
    }

    var dpr = window.devicePixelRatio || 1;
    var width = Math.round(rect.width * dpr);
    var height = Math.round(rect.height * dpr);

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    var pad = { top: 12, right: 12, bottom: 18, left: 34 };
    var plotWidth = Math.max(0, rect.width - pad.left - pad.right);
    var plotHeight = Math.max(0, rect.height - pad.top - pad.bottom);
    var i;

    /* Horizontal grid lines. */
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 1;
    for (i = 0; i <= 4; i += 1) {
      var gridY = pad.top + (plotHeight * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.left, gridY);
      ctx.lineTo(pad.left + plotWidth, gridY);
      ctx.stroke();
    }

    /* Vertical axis + zero baseline. */
    ctx.strokeStyle = AXIS_COLOR;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top);
    ctx.lineTo(pad.left, pad.top + plotHeight);
    ctx.lineTo(pad.left + plotWidth, pad.top + plotHeight);
    ctx.stroke();

    ctx.fillStyle = LABEL_COLOR;
    ctx.font = '10px "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText("0 W", pad.left - 6, pad.top + plotHeight);

    if (this.emptyEl) {
      this.emptyEl.hidden = this.samples.length > 0;
    }

    if (this.samples.length === 0) {
      return;
    }

    /* Scale to the largest real sample only. No synthetic ceiling. */
    var maxValue = Math.max.apply(null, this.samples.concat([0]));
    var scaleMax = maxValue > 0 ? maxValue * 1.1 : 1;

    ctx.fillStyle = LABEL_COLOR;
    ctx.textAlign = "right";
    ctx.fillText(
      (maxValue > 0 ? maxValue.toFixed(1) : "0") + " W",
      pad.left - 6,
      pad.top
    );

    var points = this.samples.map(function (value, index) {
      var x =
        pad.left +
        (plotWidth * index) / Math.max(1, this.samples.length - 1);
      var y = pad.top + plotHeight - (plotHeight * value) / scaleMax;
      return { x: x, y: y };
    }, this);

    /* Soft area under the real samples. */
    var gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotHeight);
    gradient.addColorStop(0, FILL_TOP);
    gradient.addColorStop(1, FILL_BOTTOM);

    ctx.beginPath();
    ctx.moveTo(points[0].x, pad.top + plotHeight);
    points.forEach(function (point) {
      ctx.lineTo(point.x, point.y);
    });
    ctx.lineTo(points[points.length - 1].x, pad.top + plotHeight);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    /* Real sample line. */
    ctx.beginPath();
    points.forEach(function (point, index) {
      if (index === 0) {
        ctx.moveTo(point.x, point.y);
      } else {
        ctx.lineTo(point.x, point.y);
      }
    });
    ctx.strokeStyle = LINE_COLOR;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    /* Last received sample marker. */
    var last = points[points.length - 1];
    ctx.beginPath();
    ctx.arc(last.x, last.y, 2.6, 0, Math.PI * 2);
    ctx.fillStyle = LINE_COLOR;
    ctx.fill();
  };

  function initCharts() {
    document.querySelectorAll("[data-power-chart]").forEach(function (canvas) {
      var chart = new PowerChart(canvas);
      canvas._powerChart = chart;
      charts.set(canvas.getAttribute("data-power-chart"), chart);
    });
  }

  window.PowerAbleChart = PowerChart;
  window.PowerAble = window.PowerAble || {};
  window.PowerAble.charts = charts;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCharts);
  } else {
    initCharts();
  }
})();
