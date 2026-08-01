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

  var input = document.getElementById("tree-search");
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

  function filterTree() {
    var query = input.value.trim().toLocaleLowerCase();
    if (!query) {
      items.forEach(function (item) {
        item.hidden = false;
      });
      matchCount.textContent = "";
      return;
    }

    items.forEach(function (item) {
      item.hidden = true;
    });
    var matches = items.filter(function (item) {
      return (item.getAttribute("data-search") || "").toLocaleLowerCase().indexOf(query) !== -1;
    });
    matches.forEach(reveal);
    matchCount.textContent = matches.length + " 项";
  }

  input.addEventListener("input", filterTree);
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
