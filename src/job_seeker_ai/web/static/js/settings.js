/* Add and remove rows in the settings editors.
 *
 * Any element marked [data-group] is a list: it owns a [data-rows] container, a
 * direct-child <template> holding one blank row, and an [data-add] button. Anything
 * marked [data-row] is removed by the [data-remove] button inside it, which is how
 * one handler covers a filter line, a deal breaker's sub-item, and the deal-breaker
 * card those sub-items sit in.
 *
 * Nothing here touches values or names: every input keeps the name it was rendered
 * with, so a plain form post arrives in document order and the server rebuilds the
 * nesting from that. The script only has to clone and remove DOM nodes.
 */
document.addEventListener("click", function (event) {
  var add = event.target.closest("[data-add]");
  if (add) {
    var group = add.closest("[data-group]");
    if (!group) {
      return;
    }
    var rows = group.querySelector(":scope > [data-rows]");
    var template = group.querySelector(":scope > template");
    if (!rows || !template) {
      return;
    }
    var row = template.content.firstElementChild.cloneNode(true);
    rows.appendChild(row);
    var first = row.querySelector("input");
    if (first) {
      first.focus();
    }
    return;
  }

  var remove = event.target.closest("[data-remove]");
  if (remove) {
    var target = remove.closest("[data-row]");
    if (target) {
      target.remove();
    }
  }
});
