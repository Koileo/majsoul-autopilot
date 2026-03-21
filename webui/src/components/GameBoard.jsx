import { Hand } from './Hand';
import { DiscardPile } from './DiscardPile';
import { InfoBar } from './InfoBar';
import { Recommendations } from './Recommendations';
import { Tile } from './Tile';
import { Meld } from './Meld';

function concealedCount(melds) {
  let count = 13;
  for (const m of melds) {
    count -= (m.consumed?.length || 0);
  }
  return Math.max(0, count);
}

function OpponentHand({ melds, actor }) {
  const backCount = concealedCount(melds);
  return (
    <div className="opponent-hand">
      <div className="opponent-hand-tiles">
        {Array.from({ length: backCount }, (_, i) => (
          <Tile key={`b-${i}`} tile="?" small faceDown />
        ))}
      </div>
      {melds.length > 0 && (
        <div className="opponent-melds">
          {melds.map((m, i) => (
            <Meld key={i} meld={m} actor={actor} small />
          ))}
        </div>
      )}
    </div>
  );
}

export function GameBoard({ state }) {
  const { playerId, scores, discards, melds, hand, tsumo, doraMarkers } = state;
  const relativeIdx = (offset) => (playerId + offset) % 4;
  const self = playerId ?? 0;
  const toimen = relativeIdx(2);
  const kamicha = relativeIdx(3);
  const shimocha = relativeIdx(1);

  const playerLabel = (idx, name) => (
    <div className="player-label">
      <span className="player-name">{name}</span>
      <span className="player-score">{scores[idx]}点</span>
    </div>
  );

  const recommendedTile = state.action?.pai || (state.top3.length > 0 ? state.top3[0][0] : null);
  const highlightTile = state.action?.type === 'dahai' ? recommendedTile : null;

  return (
    <div className="game-board">
      <InfoBar
        bakaze={state.bakaze}
        kyoku={state.kyoku}
        honba={state.honba}
        kyotaku={state.kyotaku}
        remainingTiles={state.remainingTiles}
        doraMarkers={doraMarkers}
      />
      <div className="table-area">
        <div className="player-area player-toimen">
          {playerLabel(toimen, '对家')}
          <OpponentHand melds={melds[toimen]} actor={toimen} />
          <DiscardPile discards={discards[toimen]} compact />
        </div>
        <div className="table-middle">
          <div className="player-area player-kamicha">
            {playerLabel(kamicha, '上家')}
            <OpponentHand melds={melds[kamicha]} actor={kamicha} />
            <DiscardPile discards={discards[kamicha]} compact />
          </div>
          <div className="table-center">
            <div className="center-info">
              <div className="dora-display">
                <span className="center-label">宝牌指示</span>
                <div className="dora-tiles">
                  {doraMarkers.map((d, i) => <Tile key={i} tile={d} small />)}
                </div>
              </div>
              <div className="remaining-count">{state.remainingTiles}枚</div>
            </div>
          </div>
          <div className="player-area player-shimocha">
            {playerLabel(shimocha, '下家')}
            <OpponentHand melds={melds[shimocha]} actor={shimocha} />
            <DiscardPile discards={discards[shimocha]} compact />
          </div>
        </div>
        <div className="player-area player-self">
          {playerLabel(self, '自家')}
          <DiscardPile discards={discards[self]} compact />
        </div>
      </div>
      <Recommendations
        top3={state.top3}
        shanten={state.shanten}
        furiten={state.furiten}
        action={state.action}
      />
      <div className="hand-area">
        <Hand hand={hand} tsumo={tsumo} recommendedTile={highlightTile} melds={melds[self]} actor={self} />
      </div>
    </div>
  );
}
