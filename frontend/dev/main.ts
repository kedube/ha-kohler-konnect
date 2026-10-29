import "../src/kohler-konnect-panel";
import type { KohlerKonnectPanel } from "../src/kohler-konnect-panel";
import { FakeHass } from "./fake-hass";
import { SCENARIOS } from "./scenarios";

// Query parameters drive the harness so screenshots can be taken without clicking:
// ?scenario=yours&theme=light&width=phone&path=/d/dev_shower_left&run=dev_shower_left&bare=1

const params = new URLSearchParams(location.search);
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const scenarioSelect = $<HTMLSelectElement>("scenario");
const themeSelect = $<HTMLSelectElement>("theme");
const widthSelect = $<HTMLSelectElement>("width");
const userSelect = $<HTMLSelectElement>("user");
const frame = $<HTMLDivElement>("frame");

for (const build of SCENARIOS) {
  const scenario = build();
  scenarioSelect.add(new Option(scenario.label, scenario.key));
}

scenarioSelect.value = params.get("scenario") ?? "yours";
themeSelect.value = params.get("theme") ?? "dark";
widthSelect.value = params.get("width") ?? "phone";
userSelect.value = params.get("user") ?? "admin";
if (params.get("bare") === "1") document.body.classList.add("bare");

let fake: FakeHass | undefined;
let panel: KohlerKonnectPanel | undefined;

async function mount(path = params.get("path") ?? "/") {
  fake?.dispose();
  frame.replaceChildren();
  const build = SCENARIOS.find((s) => s().key === scenarioSelect.value) ?? SCENARIOS[0];
  fake = new FakeHass(build());
  fake.darkMode = themeSelect.value === "dark";
  fake.admin = userSelect.value === "admin";
  // `lang` shows the panel in another of its languages.
  fake.language = params.get("lang") ?? "en";
  document.documentElement.style.colorScheme = fake.darkMode ? "dark" : "light";
  document.body.style.background = fake.darkMode ? "#0c0d0e" : "#dcdcd8";

  // `run` starts a device before the panel first paints, so a screenshot shows the running
  // screen as it settles rather than mid-transition from idle.
  const run = params.get("run");
  const running = run ? build().devices.find((d) => d.descriptor.id === run) : undefined;
  if (running?.kind === "valve") {
    await fake.hass.callService("switch", "turn_on", { entity_id: running.descriptor.roles.shower });
  } else if (running?.kind === "faucet") {
    await fake.hass.callService("switch", "turn_on", { entity_id: running.descriptor.roles.water });
  } else if (running?.kind === "smart_fan") {
    await fake.hass.callService("fan", "set_preset_mode", { entity_id: running.descriptor.roles.fan, preset_mode: "Steamy" });
    await fake.hass.callService("light", "turn_on", { entity_id: running.descriptor.roles.light, effect: "Morning" });
  }

  panel = document.createElement("kohler-konnect-panel") as KohlerKonnectPanel;
  panel.narrow = widthSelect.value === "phone";
  panel.route = { prefix: "/kohler-konnect", path };
  panel.navigateImpl = (next) => {
    panel!.route = { prefix: "/kohler-konnect", path: next === "/" ? "" : next };
    frame.scrollIntoView({ block: "start" });
  };
  fake.onChange = (hass) => {
    panel!.hass = hass;
  };
  panel.hass = fake.hass;
  frame.className = `frame ${widthSelect.value}`;
  frame.append(panel);
}

for (const select of [scenarioSelect, themeSelect, widthSelect, userSelect]) {
  select.addEventListener("change", () => void mount(panel?.route.path ?? "/"));
}
$("reset").addEventListener("click", () => void mount("/"));

void mount();
