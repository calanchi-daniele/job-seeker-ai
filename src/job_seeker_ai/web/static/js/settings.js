/* Dynamic rows for the Filters section.
 *
 * "Add" clones the column's <template>; "remove" deletes the row its button belongs
 * to. The listeners are delegated from the row container, so rows created later
 * work without anything being rebound.
 *
 * Nothing here submits anything: every row input shares its column's name, so a
 * plain form post sends one list per column, in document order, and the server
 * overwrites the two word lists from that.
 */
document.querySelectorAll("[data-list]").forEach(function (column) {
  var rows = column.querySelector("[data-rows]");
  var template = column.querySelector("template");
  var add = column.querySelector("[data-add]");

  add.addEventListener("click", function () {
    var row = template.content.firstElementChild.cloneNode(true);
    rows.appendChild(row);
    row.querySelector("input").focus();
  });

  rows.addEventListener("click", function (event) {
    var button = event.target.closest("[data-remove]");
    if (!button) {
      return;
    }
    button.closest(".filter-row").remove();
  });
});
