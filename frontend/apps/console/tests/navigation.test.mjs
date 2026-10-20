import assert from "node:assert/strict";
import test from "node:test";
import {
  navigateLink,
  pages,
  readPage,
  routeEditor,
  routeEditPath,
  subscribeNavigation,
} from "../src/lib/navigation.ts";

test("route configuration URLs stay in Routes and identify the requested form", () => {
  assert.equal(readPage("/routes/new"), "routes");
  assert.deepEqual(routeEditor("/routes/new"), { create: true });
  const path = routeEditPath("route_example");
  assert.equal(readPage(path), "routes");
  assert.deepEqual(routeEditor(path), { id: "route_example" });
  assert.equal(routeEditor("/routes"), null);
  assert.equal(routeEditor("/routes/route_example/edit/extra"), null);
});

function browser(t, href = "https://gateway.example/") {
  const window = new EventTarget();
  window.location = new URL(href);
  const entries = [window.location.href];
  window.history = {
    pushState(_state, _title, url) {
      window.location = new URL(url);
      entries.push(window.location.href);
    },
  };
  const original = globalThis.window;
  globalThis.window = window;
  t.after(() => {
    if (original === undefined) delete globalThis.window;
    else globalThis.window = original;
  });
  return { window, entries };
}

function click(href, overrides = {}) {
  return {
    currentTarget: { href, target: "", hasAttribute: () => false },
    button: 0,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    ...overrides,
  };
}

test("page selection uses paths and leaves root and legacy hashes unchanged", (t) => {
  const { window, entries } = browser(t, "https://gateway.example/#hosts");
  assert.equal(readPage(), "overview");
  assert.equal(window.location.href, "https://gateway.example/#hosts");
  assert.equal(entries.length, 1);
  for (const page of pages) assert.equal(readPage(`/${page}`), page);
  assert.equal(readPage("/"), "overview");
  assert.equal(readPage("/connections"), "overview");
});

test("navigation updates history and subscribers without reloading or duplicating entries", (t) => {
  const { window, entries } = browser(t);
  const visited = [];
  const unsubscribe = subscribeNavigation(() => visited.push(readPage()));
  const event = click("https://gateway.example/channels");
  navigateLink(event);
  assert.ok(event.defaultPrevented);
  assert.equal(window.location.pathname, "/channels");
  assert.deepEqual(visited, ["channels"]);
  navigateLink(click("https://gateway.example/channels"));
  assert.equal(entries.length, 2);
  assert.deepEqual(visited, ["channels"]);

  // Browser Back/Forward changes the URL before dispatching popstate.
  for (const href of [entries[0], entries[1]]) {
    window.location = new URL(href);
    window.dispatchEvent(new Event("popstate"));
  }
  assert.deepEqual(visited, ["channels", "overview", "channels"]);
  unsubscribe();
  navigateLink(click("https://gateway.example/hosts"));
  window.dispatchEvent(new Event("popstate"));
  assert.deepEqual(visited, ["channels", "overview", "channels"]);
});

test("modified clicks, new tabs, downloads and non-console links keep native behavior", (t) => {
  const { entries } = browser(t);
  for (const overrides of [
    { button: 1 },
    { metaKey: true },
    { ctrlKey: true },
    { shiftKey: true },
    { altKey: true },
    { currentTarget: { href: "https://gateway.example/hosts", target: "_blank" } },
    {
      currentTarget: {
        href: "https://gateway.example/hosts",
        target: "",
        hasAttribute: () => true,
      },
    },
  ]) {
    const event = click("https://gateway.example/hosts", overrides);
    navigateLink(event);
    assert.equal(event.defaultPrevented, false);
  }
  for (const href of [
    "https://other.example/hosts",
    "https://gateway.example/api/docs",
    "https://gateway.example/#hosts",
  ]) {
    const event = click(href);
    navigateLink(event);
    assert.equal(event.defaultPrevented, false);
  }
  navigateLink(click("https://gateway.example/hosts", { defaultPrevented: true }));
  assert.equal(entries.length, 1);
});

test("route editor navigation supports direct paths and browser history", (t) => {
  const { window, entries } = browser(t, "https://gateway.example/routes");
  const visited = [];
  const unsubscribe = subscribeNavigation(() =>
    visited.push(routeEditor(window.location.pathname)),
  );
  navigateLink(click("https://gateway.example/routes/route_example/edit"));
  navigateLink(click("https://gateway.example/routes/new?channel_id=qq"));
  assert.equal(window.location.search, "?channel_id=qq");
  window.location = new URL(entries[1]);
  window.dispatchEvent(new Event("popstate"));
  assert.deepEqual(visited, [{ id: "route_example" }, { create: true }, { id: "route_example" }]);
  unsubscribe();
});
