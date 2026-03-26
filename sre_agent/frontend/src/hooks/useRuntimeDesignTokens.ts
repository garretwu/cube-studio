import { startTransition, useEffect, useState } from "react";

import { createRuntimeDesignTokenSnapshot, type RuntimeDesignTokenSnapshot } from "../theme/runtimeDesignTokens";

const TOKEN_REFRESH_INTERVAL_MS = 2000;

function buildSnapshot() {
  return createRuntimeDesignTokenSnapshot();
}

export function useRuntimeDesignTokens(): RuntimeDesignTokenSnapshot & { refresh: () => void } {
  const [snapshot, setSnapshot] = useState<RuntimeDesignTokenSnapshot>(() => buildSnapshot());

  const refresh = () => {
    const nextSnapshot = buildSnapshot();

    startTransition(() => {
      setSnapshot((currentSnapshot) =>
        currentSnapshot.signature === nextSnapshot.signature ? currentSnapshot : nextSnapshot,
      );
    });
  };

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    let frameId = 0;
    let isAnimationFrame = false;

    const cancelScheduledRefresh = () => {
      if (isAnimationFrame) {
        window.cancelAnimationFrame(frameId);
      } else {
        window.clearTimeout(frameId);
      }
    };

    const scheduleRefresh = () => {
      cancelScheduledRefresh();

      if (typeof window.requestAnimationFrame === "function") {
        isAnimationFrame = true;
        frameId = window.requestAnimationFrame(refresh);
        return;
      }

      isAnimationFrame = false;
      frameId = window.setTimeout(refresh, 0);
    };

    scheduleRefresh();

    const rootObserver = new MutationObserver(scheduleRefresh);
    rootObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    });

    const headObserver = new MutationObserver(scheduleRefresh);
    if (document.head) {
      headObserver.observe(document.head, {
        attributes: true,
        characterData: true,
        childList: true,
        subtree: true,
      });
    }

    document.addEventListener("visibilitychange", scheduleRefresh);
    window.addEventListener("pageshow", scheduleRefresh);
    window.addEventListener("resize", scheduleRefresh);

    const intervalId = window.setInterval(refresh, TOKEN_REFRESH_INTERVAL_MS);

    return () => {
      cancelScheduledRefresh();
      window.clearInterval(intervalId);
      rootObserver.disconnect();
      headObserver.disconnect();
      document.removeEventListener("visibilitychange", scheduleRefresh);
      window.removeEventListener("pageshow", scheduleRefresh);
      window.removeEventListener("resize", scheduleRefresh);
    };
  }, []);

  return {
    ...snapshot,
    refresh,
  };
}
