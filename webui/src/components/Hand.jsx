import { Tile } from './Tile';
import { Meld } from './Meld';

export function Hand({ hand, tsumo, recommendedTile, melds, actor }) {
  return (
    <div className="hand-container">
      <div className="hand-tiles">
        {hand.map((tile, i) => (
          <Tile
            key={`h-${i}`}
            tile={tile}
            highlighted={tile === recommendedTile}
          />
        ))}
        {tsumo && (
          <>
            <div className="tsumo-gap" />
            <Tile
              tile={tsumo}
              highlighted={tsumo === recommendedTile}
            />
          </>
        )}
      </div>
      {melds && melds.length > 0 && (
        <div className="melds">
          {melds.map((meld, i) => (
            <Meld key={`m-${i}`} meld={meld} actor={actor} small />
          ))}
        </div>
      )}
    </div>
  );
}
