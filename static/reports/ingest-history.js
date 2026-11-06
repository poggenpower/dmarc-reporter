(() => {
  const selectAll = document.getElementById("select-all");
  if (!selectAll) return;
  const rowBoxes = Array.from(document.querySelectorAll("input.row-select"));
  const selectedButton = document.getElementById("backfill-selected");
  const selectedCount = document.getElementById("selected-count");

  const update = () => {
    const count = rowBoxes.filter((box) => box.checked).length;
    selectedCount.textContent = `${count} selected`;
    selectedButton.disabled = count === 0;
    selectAll.checked = count > 0 && count === rowBoxes.length;
    selectAll.indeterminate = count > 0 && count < rowBoxes.length;
  };

  selectAll.addEventListener("change", () => {
    rowBoxes.forEach((box) => { box.checked = selectAll.checked; });
    update();
  });
  rowBoxes.forEach((box) => box.addEventListener("change", update));
  update();
})();
