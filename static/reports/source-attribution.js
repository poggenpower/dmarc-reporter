(() => {
  const popover = document.querySelector("#source-popover");
  if (!popover) return;

  const details = popover.querySelector("#source-details");
  const copyButton = popover.querySelector("#source-copy");
  const copyStatus = popover.querySelector("#source-copy-status");
  const triggers = document.querySelectorAll(".source-trigger");
  let activeTrigger = null;
  let pinned = false;
  let closeTimer;

  const valuesFor = (trigger) =>
    [
      ["IP address", trigger.dataset.ip],
      ["Hostname", trigger.dataset.hostname],
      ["Network", trigger.dataset.organization],
      ["ASN", trigger.dataset.asn ? `AS${trigger.dataset.asn}` : ""],
      ["City", trigger.dataset.city],
      ["Country", trigger.dataset.country],
      ["Country code", trigger.dataset.countryCode],
    ].filter(([, value]) => value);

  const placePopover = (trigger) => {
    const bounds = trigger.getBoundingClientRect();
    const width = popover.getBoundingClientRect().width;
    const height = popover.getBoundingClientRect().height;
    const left = Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12));
    const below = bounds.bottom + 8;
    const top = below + height <= window.innerHeight - 12
      ? below
      : Math.max(12, bounds.top - height - 8);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
  };

  const closePopover = () => {
    popover.hidden = true;
    if (activeTrigger) activeTrigger.setAttribute("aria-expanded", "false");
    activeTrigger = null;
    pinned = false;
    copyStatus.textContent = "";
  };

  const showPopover = (trigger, pin = false) => {
    clearTimeout(closeTimer);
    if (activeTrigger && activeTrigger !== trigger) {
      activeTrigger.setAttribute("aria-expanded", "false");
    }
    activeTrigger = trigger;
    pinned = pin;
    trigger.setAttribute("aria-expanded", "true");
    details.replaceChildren();
    for (const [label, value] of valuesFor(trigger)) {
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value;
      details.append(term, description);
    }
    copyStatus.textContent = "";
    popover.hidden = false;
    placePopover(trigger);
  };

  const scheduleClose = () => {
    if (pinned) return;
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => {
      if (!popover.matches(":hover") && !popover.contains(document.activeElement)) {
        closePopover();
      }
    }, 140);
  };

  for (const trigger of triggers) {
    trigger.addEventListener("pointerenter", () => showPopover(trigger));
    trigger.addEventListener("pointerleave", scheduleClose);
    trigger.addEventListener("focus", () => showPopover(trigger));
    trigger.addEventListener("blur", scheduleClose);
    trigger.addEventListener("click", () => {
      if (trigger.tagName !== "BUTTON") return;
      if (activeTrigger === trigger && pinned) closePopover();
      else showPopover(trigger, true);
    });
  }

  popover.addEventListener("pointerenter", () => clearTimeout(closeTimer));
  popover.addEventListener("pointerleave", scheduleClose);
  popover.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const trigger = activeTrigger;
      closePopover();
      trigger?.focus();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!popover.contains(event.target) && !event.target.closest(".source-trigger")) {
      closePopover();
    }
  });
  window.addEventListener("resize", () => {
    if (activeTrigger && !popover.hidden) placePopover(activeTrigger);
  });
  window.addEventListener("scroll", () => {
    if (activeTrigger && !popover.hidden) placePopover(activeTrigger);
  }, true);

  copyButton.addEventListener("click", async () => {
    const text = valuesFor(activeTrigger)
      .map(([label, value]) => `${label}: ${value}`)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      copyStatus.textContent = "Details copied";
    } catch {
      copyStatus.textContent = "Clipboard access is unavailable";
    }
  });
})();
