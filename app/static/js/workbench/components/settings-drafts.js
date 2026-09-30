// Each resource keeps its own baseline; switching settings never discards drafts.
export function createSettingsDrafts(root, onChange = () => {}) {
  const resources = new Map();
  const snapshot = (element) => JSON.stringify([...element.querySelectorAll('input, select, textarea')]
    .filter((input) => !input.matches('[data-draft-ignore]'))
    .map((input) => [input.name || input.dataset.draftField || input.type, input.type === 'checkbox' || input.type === 'radio' ? input.checked : input.value]));
  function sync(resource) {
    resource.dirty = resource.loaded && snapshot(resource.element) !== resource.baseline;
    resource.element.dataset.dirty = String(resource.dirty);
    resource.buttons.forEach((button) => { button.disabled = !resource.loaded || resource.saving || !resource.dirty; });
    onChange();
  }
  const handleInput = (event) => {
    for (const resource of resources.values()) if (resource.element.contains(event.target)) sync(resource);
  };
  root.addEventListener('input', handleInput);
  root.addEventListener('change', handleInput);
  return {
    register(name, element, saveSelector = '[type="submit"]') {
      element.inert = true;
      resources.set(name, { element, buttons: [...element.querySelectorAll(saveSelector)], loaded: false, dirty: false, saving: false, baseline: '' });
      sync(resources.get(name));
    },
    accept(name) {
      const resource = resources.get(name);
      resource.element.inert = false;
      resource.loaded = true; resource.baseline = snapshot(resource.element); sync(resource);
    },
    sync(name) { sync(resources.get(name)); },
    dirty(name) { return Boolean(resources.get(name)?.dirty); },
    saving(name) { return Boolean(resources.get(name)?.saving); },
    async save(name, action) {
      const resource = resources.get(name);
      if (!resource.loaded || resource.saving || !resource.dirty) return;
      resource.saving = true;
      // Disable editing during the request so a successful response cannot erase newer input.
      const controls = [...resource.element.querySelectorAll('input, select, textarea, button')];
      const disabled = controls.map((input) => input.disabled);
      onChange();
      try {
        // Start the handler first so FormData sees the enabled controls.
        const pending = action();
        controls.forEach((input) => { input.disabled = true; });
        await pending;
      }
      finally {
        controls.forEach((input, index) => { input.disabled = disabled[index]; });
        resource.saving = false; sync(resource);
      }
    },
    get hasDirty() { return [...resources.values()].some((resource) => resource.dirty); },
    get busy() { return [...resources.values()].some((resource) => resource.saving); },
    cleanup() { root.removeEventListener('input', handleInput); root.removeEventListener('change', handleInput); resources.clear(); },
  };
}
