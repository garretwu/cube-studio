import "@testing-library/jest-dom/vitest";

import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

class ResizeObserverMock {
  observe() {}

  unobserve() {}

  disconnect() {}
}

class IntersectionObserverMock {
  observe() {}

  unobserve() {}

  disconnect() {}

  takeRecords() {
    return [];
  }
}
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

if (!window.ResizeObserver) {
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: ResizeObserverMock,
  });
}

if (!global.ResizeObserver) {
  Object.defineProperty(global, "ResizeObserver", {
    writable: true,
    value: ResizeObserverMock,
  });
}

if (!window.IntersectionObserver) {
  Object.defineProperty(window, "IntersectionObserver", {
    writable: true,
    value: IntersectionObserverMock,
  });
}

if (!global.IntersectionObserver) {
  Object.defineProperty(global, "IntersectionObserver", {
    writable: true,
    value: IntersectionObserverMock,
  });
}

const svgElementPrototype = window.SVGElement.prototype as SVGElement & {
  getBBox?: () => { x: number; y: number; width: number; height: number };
};

if (!svgElementPrototype.getBBox) {
  svgElementPrototype.getBBox = () => ({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  });
}

if (typeof ProgressEvent === "undefined") {
  class ProgressEventMock extends Event {
    constructor(type: string, eventInitDict?: EventInit) {
      super(type, eventInitDict);
    }
  }

  Object.defineProperty(globalThis, "ProgressEvent", {
    writable: true,
    value: ProgressEventMock,
  });
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

