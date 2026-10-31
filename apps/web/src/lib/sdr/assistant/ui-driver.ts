/** Lets the assistant operate any visible control in the app, the way a user would. */
const SELECTOR = [
  "button", "a[href]", "input", "select", "textarea", "summary",
  "[role=button]", "[role=tab]", "[role=switch]", "[role=checkbox]", "[role=menuitem]", "[role=slider]", "[role=option]",
].join(",");
const MAX_ELEMENTS = 60;
const DESTRUCTIVE = /\b(delete|remove|erase|reset|clear|forget|wipe|discard)\b/i;

const registry = new Map<string, WeakRef<HTMLElement>>();

function visible(el: HTMLElement): boolean {
  if (el.closest(".assistant-bar")) return false;
  if (el.getClientRects().length === 0) return false;
  const style = getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none";
}

function labelOf(el: HTMLElement): string {
  const aria = el.getAttribute("aria-label");
  if (aria) return aria.trim();
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim();
    if (text) return text;
  }
  const labels = (el as HTMLInputElement).labels;
  if (labels?.length) return Array.from(labels, (l) => l.textContent ?? "").join(" ").trim();
  const text = el.textContent?.replace(/\s+/g, " ").trim();
  if (text) return text.slice(0, 80);
  return (el.getAttribute("title") ?? el.getAttribute("placeholder") ?? el.getAttribute("name") ?? "").trim();
}

function roleOf(el: HTMLElement): string {
  const role = el.getAttribute("role");
  if (role) return role;
  if (el instanceof HTMLInputElement) return el.type === "checkbox" ? "checkbox" : el.type === "radio" ? "radio" : el.type === "range" ? "slider" : `input:${el.type}`;
  return el.tagName.toLowerCase();
}

function describeElement(id: string, el: HTMLElement) {
  const out: Record<string, unknown> = { id, role: roleOf(el), label: labelOf(el) };
  if (el instanceof HTMLInputElement) {
    if (el.type === "checkbox" || el.type === "radio") out.checked = el.checked;
    else out.value = el.value;
    if (el.type === "range" || el.type === "number") { out.min = el.min; out.max = el.max; out.step = el.step; }
  } else if (el instanceof HTMLSelectElement) {
    out.value = el.value;
    out.options = Array.from(el.options, (o) => o.text).slice(0, 14);
  } else if (el instanceof HTMLTextAreaElement) out.value = el.value.slice(0, 80);
  const pressed = el.getAttribute("aria-pressed") ?? el.getAttribute("aria-selected") ?? el.getAttribute("aria-checked");
  if (pressed) out.on = pressed === "true";
  if ((el as HTMLButtonElement).disabled) out.disabled = true;
  return out;
}

export function uiSnapshot(filter?: unknown) {
  registry.clear();
  const needle = typeof filter === "string" ? filter.trim().toLowerCase() : "";
  const found = Array.from(document.querySelectorAll<HTMLElement>(SELECTOR))
    .filter(visible)
    .map((el) => ({ el, label: labelOf(el) }))
    .filter(({ el, label }) => label && (!needle || `${label} ${roleOf(el)}`.toLowerCase().includes(needle)));
  const elements = found.slice(0, MAX_ELEMENTS).map(({ el }, index) => {
    const id = `e${index + 1}`;
    registry.set(id, new WeakRef(el));
    return describeElement(id, el);
  });
  return { total: found.length, shown: elements.length, elements };
}

function lookup(id: unknown): HTMLElement {
  const el = typeof id === "string" ? registry.get(id)?.deref() : undefined;
  if (!el || !el.isConnected) throw new Error("Unknown or stale element id; call ui_snapshot again");
  return el;
}

export function uiClick(id: unknown, confirm: unknown) {
  const el = lookup(id);
  const label = labelOf(el);
  if (DESTRUCTIVE.test(label) && confirm !== true) {
    throw new Error(`"${label}" looks destructive. Ask the user to confirm, then retry with confirm=true.`);
  }
  if ((el as HTMLButtonElement).disabled) throw new Error(`"${label}" is disabled`);
  el.scrollIntoView({ block: "nearest" });
  el.click();
  return describeElement(String(id), el);
}

export function uiSet(id: unknown, value: unknown) {
  const el = lookup(id);
  if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") throw new Error("value must be a string, number or boolean");
  if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
    if (el.checked !== Boolean(value)) el.click();
    return describeElement(String(id), el);
  }
  if (!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement)) {
    throw new Error("Element has no value; use ui_click");
  }
  let next = String(value);
  if (el instanceof HTMLSelectElement) {
    const want = next.toLowerCase();
    const option = Array.from(el.options).find((o) => o.value.toLowerCase() === want || o.text.trim().toLowerCase() === want)
      ?? Array.from(el.options).find((o) => o.text.toLowerCase().includes(want));
    if (!option) throw new Error(`No option "${next}". Options: ${Array.from(el.options, (o) => o.text).join(", ")}`);
    next = option.value;
  }
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  // React tracks the value setter; go through the prototype so it sees the change.
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, next);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return describeElement(String(id), el);
}

export function uiKey(args: Record<string, unknown>) {
  const key = args.key;
  if (typeof key !== "string" || !key) throw new Error("key is required");
  const init = { key, bubbles: true, ctrlKey: args.ctrl === true, shiftKey: args.shift === true, altKey: args.alt === true };
  const target = document.activeElement && document.activeElement !== document.body ? document.activeElement : document.body;
  target.dispatchEvent(new KeyboardEvent("keydown", init));
  target.dispatchEvent(new KeyboardEvent("keyup", init));
  return { sent: key };
}

export function uiRead(filter?: unknown) {
  const root = document.querySelector<HTMLElement>(".sdr-shell") ?? document.body;
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".assistant-bar, script, style, canvas").forEach((n) => n.remove());
  let text = (clone.innerText || clone.textContent || "").replace(/\n{2,}/g, "\n").trim();
  if (typeof filter === "string" && filter.trim()) {
    const needle = filter.trim().toLowerCase();
    text = text.split("\n").filter((line) => line.toLowerCase().includes(needle)).join("\n");
  }
  return { text: text.slice(0, 3_000) };
}
