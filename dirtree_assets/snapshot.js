(function () {
  "use strict";

  var statsNode = document.getElementById("snapshot-stats");
  var stats = JSON.parse(statsNode.textContent);
  Object.keys(stats).forEach(function (key) {
    var target = document.querySelector('[data-stat="' + key + '"]');
    if (target) {
      target.textContent = String(stats[key]);
    }
  });

  var fileItems = Array.prototype.slice.call(document.querySelectorAll(".file-item"));
  var totalBytes = fileItems.reduce(function (total, item) {
    return total + (Number(item.getAttribute("data-size")) || 0);
  }, 0);
  var bytesTarget = document.querySelector('[data-stat="bytes"]');
  var byteUnits = ["B", "KiB", "MiB", "GiB", "TiB"];
  function formatBytes(value) {
    var amount = value;
    var unit = 0;
    while (amount >= 1024 && unit < byteUnits.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return (unit === 0 ? String(Math.round(amount)) : amount.toFixed(1)) + " " + byteUnits[unit];
  }
  if (bytesTarget) {
    bytesTarget.textContent = formatBytes(totalBytes);
  }

  var input = document.getElementById("tree-search");
  var kindFilter = document.getElementById("kind-filter");
  var extensionFilter = document.getElementById("extension-filter");
  var minSizeFilter = document.getElementById("min-size-filter");
  var maxSizeFilter = document.getElementById("max-size-filter");
  var matchCount = document.getElementById("match-count");
  var items = Array.prototype.slice.call(document.querySelectorAll(".tree-item"));
  var rootDetails = document.querySelector(".root-item > details");

  function reveal(item) {
    item.hidden = false;
    var parentDetails = item.parentElement.closest("details");
    while (parentDetails) {
      parentDetails.open = true;
      var parentItem = parentDetails.closest(".tree-item");
      if (!parentItem) {
        break;
      }
      parentItem.hidden = false;
      parentDetails = parentItem.parentElement.closest("details");
    }
  }

  function sizeMatches(item, minimum, maximum) {
    if (minimum === null && maximum === null) {
      return true;
    }
    if (item.getAttribute("data-kind") !== "file") {
      return false;
    }
    var size = Number(item.getAttribute("data-size")) || 0;
    return (minimum === null || size >= minimum) && (maximum === null || size <= maximum);
  }

  function extensionMatches(item, extensions) {
    if (!extensions.length) {
      return true;
    }
    if (item.getAttribute("data-kind") !== "file") {
      return false;
    }
    var path = (item.getAttribute("data-path") || "").toLocaleLowerCase();
    return extensions.some(function (extension) {
      return path.endsWith(extension);
    });
  }

  function filterTree() {
    var query = input.value.trim().toLocaleLowerCase();
    var kind = kindFilter.value;
    var extensions = extensionFilter.value.split(/[,\s]+/).map(function (value) {
      var normalized = value.trim().toLocaleLowerCase();
      return normalized && normalized.charAt(0) !== "." ? "." + normalized : normalized;
    }).filter(Boolean);
    var minimum = minSizeFilter.value === "" ? null : Number(minSizeFilter.value) * 1024;
    var maximum = maxSizeFilter.value === "" ? null : Number(maxSizeFilter.value) * 1024;
    var candidates = items.filter(function (item) {
      var kindMatch = kind === "all" || item.getAttribute("data-kind") === kind;
      var queryMatch = !query || (item.getAttribute("data-search") || "").toLocaleLowerCase().indexOf(query) !== -1;
      return kindMatch && queryMatch && extensionMatches(item, extensions) && sizeMatches(item, minimum, maximum);
    });
    items.forEach(function (item) {
      item.hidden = true;
    });
    candidates.forEach(reveal);
    matchCount.textContent = (query || kind !== "all" || extensions.length || minimum !== null || maximum !== null) ? candidates.length + " 项" : "";
  }

  input.addEventListener("input", filterTree);
  kindFilter.addEventListener("change", filterTree);
  extensionFilter.addEventListener("input", filterTree);
  minSizeFilter.addEventListener("input", filterTree);
  maxSizeFilter.addEventListener("input", filterTree);
  input.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      input.value = "";
      filterTree();
      input.blur();
    }
  });

  document.getElementById("expand-all").addEventListener("click", function () {
    document.querySelectorAll("details").forEach(function (details) {
      details.open = true;
    });
  });

  document.getElementById("collapse-all").addEventListener("click", function () {
    document.querySelectorAll("details").forEach(function (details) {
      details.open = false;
    });
    if (rootDetails) {
      rootDetails.open = true;
    }
  });

  function fallbackCopy(value) {
    var area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".copy-path, .copy-value");
    if (!button) {
      return;
    }
    var value = button.getAttribute("data-copy-path") || button.getAttribute("data-copy-value") || "";
    var copy = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(value) : Promise.resolve().then(function () { fallbackCopy(value); });
    copy.then(function () {
      var original = button.getAttribute("title");
      button.setAttribute("title", "已复制");
      window.setTimeout(function () { button.setAttribute("title", original || "复制"); }, 1200);
    }).catch(function () { fallbackCopy(value); });
  });

  var themeToggle = document.getElementById("theme-toggle");
  var savedTheme = null;
  try { savedTheme = window.localStorage.getItem("dirtree-theme"); } catch (error) { savedTheme = null; }
  if (savedTheme === "dark") {
    document.body.setAttribute("data-theme", "dark");
  }
  themeToggle.addEventListener("click", function () {
    var dark = document.body.getAttribute("data-theme") === "dark";
    document.body.setAttribute("data-theme", dark ? "light" : "dark");
    try { window.localStorage.setItem("dirtree-theme", dark ? "light" : "dark"); } catch (error) {}
  });

  var printState = [];
  window.addEventListener("beforeprint", function () {
    printState = Array.prototype.map.call(document.querySelectorAll("details"), function (details) {
      var wasOpen = details.open;
      details.open = true;
      return wasOpen;
    });
  });
  window.addEventListener("afterprint", function () {
    document.querySelectorAll("details").forEach(function (details, index) {
      details.open = printState[index];
    });
  });
}());
