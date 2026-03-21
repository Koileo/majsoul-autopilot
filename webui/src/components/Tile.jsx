import { tileSvgPath } from '../utils/tiles';
import './Tile.css';

export function Tile({ tile, highlighted = false, small = false, faceDown = false, sideways = false }) {
  if (faceDown || !tile || tile === '?') {
    return (
      <div className={`tile tile-back ${small ? 'tile-sm' : ''}`}>
        <img src="/tiles/Back.svg" alt="?" className="tile-img" draggable={false} />
      </div>
    );
  }

  const classes = [
    'tile',
    small ? 'tile-sm' : '',
    highlighted ? 'tile-highlighted' : '',
    sideways ? 'tile-sideways' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={classes} title={tile}>
      <img src={tileSvgPath(tile)} alt={tile} className="tile-img" draggable={false} />
    </div>
  );
}
