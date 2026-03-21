import { tileSort } from '../utils/tiles';

export const initialState = {
  playerId: null,
  kyoku: 0,
  honba: 0,
  kyotaku: 0,
  bakaze: 'E',
  scores: [25000, 25000, 25000, 25000],
  doraMarkers: [],
  discards: [[], [], [], []],
  melds: [[], [], [], []],
  reachPending: [false, false, false, false],
  hand: [],
  tsumo: null,
  remainingTiles: 70,
  shanten: null,
  furiten: false,
  action: null,
  top3: [],
  connected: false,
};

export function gameReducer(state, action) {
  switch (action.type) {
    case 'FULL_STATE':
      return processFullState(state, action.payload);
    case 'GAME_EVENT':
      return processGameEvent(state, action.payload);
    case 'INFERENCE':
      return processInference(state, action.payload);
    case 'SET_CONNECTED':
      return { ...state, connected: action.payload };
    default:
      return state;
  }
}

function processFullState(state, payload) {
  let newState = { ...initialState, playerId: payload.player_id, connected: true };
  for (const event of (payload.game_flow || [])) {
    newState = processGameEvent(newState, event);
  }
  for (const inf of (payload.inference || [])) {
    newState = processInference(newState, inf);
  }
  return newState;
}

function processGameEvent(state, event) {
  const s = { ...state };
  switch (event.type) {
    case 'start_game':
      s.playerId = event.id;
      break;
    case 'start_kyoku':
      s.kyoku = event.kyoku;
      s.honba = event.honba;
      s.kyotaku = event.kyotaku || 0;
      s.bakaze = event.bakaze;
      s.scores = [...event.scores];
      s.doraMarkers = [event.dora_marker];
      s.discards = [[], [], [], []];
      s.melds = [[], [], [], []];
      s.reachPending = [false, false, false, false];
      s.remainingTiles = event.scores[3] === 0 ? 55 : 70;
      if (s.playerId != null && event.tehais && event.tehais[s.playerId]) {
        s.hand = [...event.tehais[s.playerId]].sort(tileSort);
        s.tsumo = null;
      }
      s.shanten = null;
      s.furiten = false;
      s.action = null;
      s.top3 = [];
      break;
    case 'tsumo':
      s.remainingTiles = Math.max(0, s.remainingTiles - 1);
      if (event.actor === s.playerId && event.pai !== '?') {
        // Store tsumo — displayed separately at right end
        s.tsumo = event.pai;
      }
      break;
    case 'reach':
      s.reachPending = s.reachPending.map((v, i) => i === event.actor ? true : v);
      break;
    case 'dahai': {
      s.action = null;
      s.top3 = [];
      const isRiichi = s.reachPending[event.actor] || false;
      if (isRiichi) {
        s.reachPending = s.reachPending.map((v, i) => i === event.actor ? false : v);
      }
      s.discards = s.discards.map((d, i) =>
        i === event.actor ? [...d, { pai: event.pai, tsumogiri: event.tsumogiri, isRiichi }] : d
      );
      if (event.actor === s.playerId) {
        if (event.tsumogiri && s.tsumo === event.pai) {
          // Discarded the tsumo directly — hand unchanged
          s.tsumo = null;
        } else {
          // Discarded from hand — merge tsumo, remove discarded, re-sort
          const newHand = [...s.hand];
          if (s.tsumo) {
            newHand.push(s.tsumo);
            s.tsumo = null;
          }
          const idx = newHand.indexOf(event.pai);
          if (idx >= 0) newHand.splice(idx, 1);
          s.hand = newHand.sort(tileSort);
        }
      }
      break;
    }
    case 'chi':
    case 'pon':
    case 'daiminkan':
    case 'kakan':
    case 'ankan':
      s.action = null;
      s.top3 = [];
      if (event.type === 'kakan') {
        // Upgrade existing pon to kan — replace the pon entry, don't add a new one
        s.melds = s.melds.map((m, i) =>
          i === event.actor
            ? m.map(meld =>
                meld.type === 'pon' && meld.pai === event.pai
                  ? { ...meld, type: 'kakan', consumed: [...meld.consumed, event.pai] }
                  : meld
              )
            : m
        );
      } else {
        s.melds = s.melds.map((m, i) =>
          i === event.actor
            ? [...m, { type: event.type, pai: event.pai, consumed: event.consumed, target: event.target }]
            : m
        );
      }
      if (event.type !== 'ankan' && event.type !== 'kakan' && event.target != null) {
        s.discards = s.discards.map((d, i) =>
          i === event.target ? d.slice(0, -1) : d
        );
      }
      if (event.actor === s.playerId && event.consumed) {
        const newHand = [...s.hand];
        for (const tile of event.consumed) {
          const idx = newHand.indexOf(tile);
          if (idx >= 0) newHand.splice(idx, 1);
        }
        s.hand = newHand.sort(tileSort);
      }
      break;
    case 'dora':
      s.doraMarkers = [...s.doraMarkers, event.dora_marker];
      break;
    default:
      break;
  }
  return s;
}

function processInference(state, data) {
  if (!data.tehai) {
    return {
      ...state,
      shanten: data.shanten,
      furiten: data.furiten,
      action: data.action,
      top3: data.top3 || [],
    };
  }

  const handTiles = [...data.tehai];
  let tsumo = null;

  // If we have a tsumo tile from the game event, try to separate it
  // This works for any hand size (14 after normal draw, 11 after pon, 8 after double meld, etc.)
  if (state.tsumo) {
    const idx = handTiles.indexOf(state.tsumo);
    if (idx >= 0) {
      handTiles.splice(idx, 1);
      tsumo = state.tsumo;
    }
  }

  return {
    ...state,
    hand: handTiles.sort(tileSort),
    tsumo,
    shanten: data.shanten,
    furiten: data.furiten,
    action: data.action,
    top3: data.top3 || [],
  };
}
