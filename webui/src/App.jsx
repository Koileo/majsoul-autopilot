import { useReducer } from 'react';
import { gameReducer, initialState } from './state/gameReducer';
import { useGameWebSocket } from './hooks/useWebSocket';
import { GameBoard } from './components/GameBoard';
import './App.css';

function App() {
  const [state, dispatch] = useReducer(gameReducer, initialState);
  useGameWebSocket(dispatch);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Akagi</h1>
        <div className="connection-status">
          <span className={`status-dot ${state.connected ? 'connected' : 'disconnected'}`} />
          {state.connected ? '已连接' : '未连接'}
        </div>
      </header>
      <main className="app-main">
        {state.playerId != null ? (
          <GameBoard state={state} />
        ) : (
          <div className="waiting">
            <p>等待对局开始...</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
