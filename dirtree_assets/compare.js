(function () {
  "use strict";
  var search = document.getElementById("diff-search");
  var filter = document.getElementById("status-filter");
  var count = document.getElementById("match-count");
  var rows = Array.prototype.slice.call(document.querySelectorAll("tbody tr"));

  function applyFilters() {
    var query = search.value.trim().toLocaleLowerCase();
    var status = filter.value;
    var visible = 0;
    rows.forEach(function (row) {
      var rowStatus = row.getAttribute("data-status");
      var statusMatch = status === "all" || (status === "differences" ? rowStatus !== "same" : rowStatus === status);
      var searchMatch = !query || (row.getAttribute("data-search") || "").toLocaleLowerCase().indexOf(query) !== -1;
      row.hidden = !(statusMatch && searchMatch);
      if (!row.hidden) visible += 1;
    });
    count.textContent = visible + " 项";
  }

  search.addEventListener("input", applyFilters);
  search.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      search.value = "";
      applyFilters();
      search.blur();
    }
  });
  filter.addEventListener("change", applyFilters);
  filter.value = document.body.getAttribute("data-initial-filter") || "differences";
  applyFilters();
}());
