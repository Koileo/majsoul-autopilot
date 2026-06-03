// Tile code → SVG filename mapping
const TILE_SVG_MAP = {
  '1m': 'Man1', '2m': 'Man2', '3m': 'Man3', '4m': 'Man4', '5m': 'Man5',
  '6m': 'Man6', '7m': 'Man7', '8m': 'Man8', '9m': 'Man9', '5mr': 'Man5-Dora',
  '1p': 'Pin1', '2p': 'Pin2', '3p': 'Pin3', '4p': 'Pin4', '5p': 'Pin5',
  '6p': 'Pin6', '7p': 'Pin7', '8p': 'Pin8', '9p': 'Pin9', '5pr': 'Pin5-Dora',
  '1s': 'Sou1', '2s': 'Sou2', '3s': 'Sou3', '4s': 'Sou4', '5s': 'Sou5',
  '6s': 'Sou6', '7s': 'Sou7', '8s': 'Sou8', '9s': 'Sou9', '5sr': 'Sou5-Dora',
  'E': 'Ton', 'S': 'Nan', 'W': 'Shaa', 'N': 'Pei',
  'P': 'Haku', 'F': 'Hatsu', 'C': 'Chun',
};

/** Get SVG path for a tile code */
export function tileSvgPath(tileStr) {
  const name = TILE_SVG_MAP[tileStr];
  if (name) return `/tiles/${name}.svg`;
  return `/tiles/Back.svg`;
}

const WIND_TILES = { E: '东', S: '南', W: '西', N: '北' };
const DRAGON_TILES = { P: '白', F: '发', C: '中' };
const NUM_KANJI = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
const SUIT_KANJI = { m: '万', p: '筒', s: '索' };

/** Convert tile code to human-readable Chinese text */
export function tileToText(tileStr) {
  if (!tileStr || tileStr === '?') return '?';
  if (DRAGON_TILES[tileStr]) return DRAGON_TILES[tileStr];
  if (WIND_TILES[tileStr]) return WIND_TILES[tileStr];
  const isRed = tileStr.endsWith('r');
  const base = isRed ? tileStr.slice(0, -1) : tileStr;
  const number = parseInt(base[0]);
  const suit = base[1];
  const prefix = isRed ? '赤' : '';
  return prefix + (NUM_KANJI[number] || number) + (SUIT_KANJI[suit] || suit);
}

/** Sort tiles by suit (万→筒→索→字) then by number */
export function tileSort(a, b) {
  const aInfo = parseTileForSort(a);
  const bInfo = parseTileForSort(b);

  if (aInfo.suitIdx !== bInfo.suitIdx) return aInfo.suitIdx - bInfo.suitIdx;
  return aInfo.num - bInfo.num;
}

function parseTileForSort(tileStr) {
  const suitOrder = { m: 0, p: 1, s: 2 };
  const honorOrder = { E: 30, S: 31, W: 32, N: 33, P: 34, F: 35, C: 36 };

  if (honorOrder[tileStr] != null) {
    return { suitIdx: 3, num: honorOrder[tileStr] };
  }
  const isRed = tileStr.endsWith('r');
  const base = isRed ? tileStr.slice(0, -1) : tileStr;
  const number = parseInt(base[0]);
  const suit = base[1];
  // Red 5 sorts just after normal 5 (5, 5r, 6...)
  const num = isRed ? number + 0.5 : number;
  return { suitIdx: suitOrder[suit] ?? 4, num };
}

export const ACTION_NAMES = {
  dahai: '打', hora: '和了', pon: '碰',
  chi_low: '吃', chi_mid: '吃', chi_high: '吃',
  kan_select: '杠', reach: '立直', ryukyoku: '流局',
  none: '跳过', nukidora: '拔北',
};

export const ACTION_COLORS = {
  hora: '#DC2626', pon: '#2563EB',
  chi_low: '#16A34A', chi_mid: '#16A34A', chi_high: '#16A34A',
  kan_select: '#9333EA', reach: '#F59E0B',
  none: '#9CA3AF', dahai: '#D97706',
};
