import { Tile } from './Tile';

export function DiscardPile({ discards, compact = false }) {
  return (
    <div className={`discard-pile ${compact ? 'discard-compact' : ''}`}>
      {discards.map((d, i) => (
        <Tile
          key={`d-${i}`}
          tile={d.pai || d}
          small={compact}
          sideways={d.isRiichi || false}
        />
      ))}
    </div>
  );
}
