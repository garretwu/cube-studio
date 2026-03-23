import { useEffect, useMemo, useState } from "react";

import { ManagedWebSocket } from "../api/ws";
import type { WSEvent } from "../api/types";

type UseWebSocketOptions = {
  enabled?: boolean;
  maxBufferedMessages?: number;
};

export function useWebSocket(url: string, onEvent: (event: WSEvent) => void, options: UseWebSocketOptions = {}) {
  const [state, setState] = useState<"connecting" | "open" | "closed" | "error">("closed");

  const client = useMemo(
    () =>
      new ManagedWebSocket(url, {
        maxBufferedMessages: options.maxBufferedMessages,
        onEvent,
        onStateChange: setState,
      }),
    [onEvent, options.maxBufferedMessages, url],
  );

  useEffect(() => {
    if (options.enabled === false) {
      return undefined;
    }
    client.connect();
    return () => client.close();
  }, [client, options.enabled]);

  return { state };
}
