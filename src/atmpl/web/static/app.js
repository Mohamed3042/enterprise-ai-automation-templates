/* Row click selects; only labelled buttons act or navigate. */
document.addEventListener("click", function (event) {
  var row = event.target.closest("table[data-selectable] tbody tr");
  if (!row) return;
  if (event.target.closest("button, a, input, form")) return;
  var body = row.parentElement;
  Array.prototype.forEach.call(body.children, function (sibling) {
    sibling.classList.toggle("is-selected", sibling === row);
  });
});

document.addEventListener("keydown", function (event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  var row = event.target.closest("table[data-selectable] tbody tr");
  if (!row || event.target !== row) return;
  event.preventDefault();
  row.click();
});
