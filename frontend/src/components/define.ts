/**
 * Register a custom element unless one with that name already exists. Home Assistant can
 * load the panel module more than once (a reconnect, a second account), and a second
 * `define` of the same name would throw.
 */
export function define(name: string, constructor: CustomElementConstructor): void {
  if (!customElements.get(name)) customElements.define(name, constructor);
}
