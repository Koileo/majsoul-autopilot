import { useCallback, useEffect, useRef } from 'react';

export function useGameWebSocket(dispatch) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const pollTimer = useRef(null);
  const shouldReconnect = useRef(true);
  const lastMessageAt = useRef(0);

  const syncState = useCallback(async () => {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' });
      if (!response.ok) return;
      const state = await response.json();
      dispatch({ type: 'FULL_STATE', payload: state });
    } catch {
      dispatch({ type: 'SET_CONNECTED', payload: false });
    }
  }, [dispatch]);

  useEffect(() => {
    const connect = () => {
      if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
        return;
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        dispatch({ type: 'SET_CONNECTED', payload: true });
      };

      ws.onmessage = (event) => {
        try {
          lastMessageAt.current = Date.now();
          const msg = JSON.parse(event.data);
          switch (msg.type) {
            case 'full_state':
              dispatch({ type: 'FULL_STATE', payload: msg });
              break;
            case 'game_event':
              dispatch({ type: 'GAME_EVENT', payload: msg.data });
              break;
            case 'inference':
              dispatch({ type: 'INFERENCE', payload: msg.data });
              break;
          }
        } catch (e) {
          console.error('Failed to parse WebSocket message:', e);
        }
      };

      ws.onclose = () => {
        dispatch({ type: 'SET_CONNECTED', payload: false });
        wsRef.current = null;
        if (shouldReconnect.current) {
          reconnectTimer.current = setTimeout(connect, 2000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    shouldReconnect.current = true;
    syncState();
    connect();

    pollTimer.current = setInterval(() => {
      const ws = wsRef.current;
      const socketOpen = ws && ws.readyState === WebSocket.OPEN;
      const stale = Date.now() - lastMessageAt.current > 3000;
      if (!socketOpen || stale) {
        syncState();
      }
    }, 2000);

    return () => {
      shouldReconnect.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (pollTimer.current) clearInterval(pollTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [dispatch, syncState]);

  return wsRef;
}
