import { tileToText } from '../utils/tiles';

const BAKAZE_MAP = { E: '东', S: '南', W: '西', N: '北' };

export function InfoBar({ bakaze, kyoku, honba, kyotaku, remainingTiles, doraMarkers }) {
  return (
    <div className="info-bar">
      <span className="info-item">{BAKAZE_MAP[bakaze] || bakaze}{kyoku}局</span>
      <span className="info-item">{honba}本场</span>
      {kyotaku > 0 && <span className="info-item">供托: {kyotaku}</span>}
      <span className="info-item">剩余: {remainingTiles}枚</span>
      <span className="info-item">宝牌: {doraMarkers.map(d => tileToText(d)).join(', ')}</span>
    </div>
  );
}
