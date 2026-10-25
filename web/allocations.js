/* permitA38 – Allocations dialog (planner per project). Entry points: the project page and the team page call
   openAllocations({projectId?, userId?}). Filled in by the following commits. */
(() => {
  window.openAllocations = () => {};
  A.allocations = el => window.openAllocations({ projectId: el.dataset.project || null, userId: el.dataset.user || null });
})();
