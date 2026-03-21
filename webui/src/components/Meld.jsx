import { Tile } from './Tile';

/**
 * Render a single meld with the called tile sideways and positioned
 * to indicate which player it was taken from.
 *
 * relativePos = (target - actor + 4) % 4
 *   3 = kamicha (left)  → called tile on left
 *   2 = toimen  (mid)   → called tile in middle
 *   1 = shimocha (right) → called tile on right
 */
export function Meld({ meld, actor, small = false }) {
  const { type, pai, consumed = [], target } = meld;

  // ankan: no called tile from another player
  if (type === 'ankan') {
    return (
      <div className="meld">
        {consumed.map((t, j) => <Tile key={j} tile={t} small={small} />)}
      </div>
    );
  }

  if (!pai || target == null) {
    // Fallback: just render consumed + pai
    return (
      <div className="meld">
        {consumed.map((t, j) => <Tile key={j} tile={t} small={small} />)}
        {pai && <Tile tile={pai} small={small} />}
      </div>
    );
  }

  const relativePos = (target - actor + 4) % 4;
  const calledTile = <Tile key="called" tile={pai} small={small} sideways />;

  let tiles;
  if (relativePos === 3) {
    // kamicha: called tile on the left
    tiles = [calledTile, ...consumed.map((t, j) => <Tile key={j} tile={t} small={small} />)];
  } else if (relativePos === 2) {
    // toimen: called tile in the middle
    tiles = [
      <Tile key={0} tile={consumed[0]} small={small} />,
      calledTile,
      ...consumed.slice(1).map((t, j) => <Tile key={j + 1} tile={t} small={small} />),
    ];
  } else {
    // shimocha: called tile on the right
    tiles = [...consumed.map((t, j) => <Tile key={j} tile={t} small={small} />), calledTile];
  }

  return <div className="meld">{tiles}</div>;
}
