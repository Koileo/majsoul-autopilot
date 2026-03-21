import { ACTION_NAMES, ACTION_COLORS, tileToText } from '../utils/tiles';

const CALL_ACTIONS = new Set([
  'hora', 'pon', 'chi', 'chi_low', 'chi_mid', 'chi_high',
  'kan_select', 'kakan', 'ankan', 'daiminkan',
  'reach', 'ryukyoku', 'none', 'nukidora',
]);

const CALL_NAMES = {
  ...ACTION_NAMES,
  kakan: '杠', ankan: '杠', daiminkan: '杠', chi: '吃',
};

// Soft, warm colors that match the pale yellow theme
const CALL_COLORS = {
  hora: '#C0392B', pon: '#2471A3', chi: '#1E8449',
  chi_low: '#1E8449', chi_mid: '#1E8449', chi_high: '#1E8449',
  kan_select: '#7D3C98', kakan: '#7D3C98', ankan: '#7D3C98', daiminkan: '#7D3C98',
  reach: '#B7950B', none: '#7F8C8D', ryukyoku: '#7F8C8D', nukidora: '#B7950B',
};

function isCallAction(type) {
  return CALL_ACTIONS.has(type);
}

function getCallColor(type) {
  return CALL_COLORS[type] || '#D97706';
}

export function Recommendations({ top3, shanten, furiten, action }) {
  const showCallBanner = action && isCallAction(action.type);
  const heroColor = showCallBanner ? getCallColor(action.type) : null;

  return (
    <div className={`recommendations ${showCallBanner ? 'recommendations-call' : ''}`}>
      <div className="rec-status">
        {shanten != null && (
          <span className="status-item">向听: <strong>{shanten}</strong></span>
        )}
        <span className="status-item">
          振听: <strong style={{ color: furiten ? '#DC2626' : '#16A34A' }}>
            {furiten ? '是' : '否'}
          </strong>
        </span>
        {action && !showCallBanner && (
          <span className="status-item">
            推荐:
            <strong style={{ color: ACTION_COLORS[action.type] || '#D97706' }}>
              {' '}{ACTION_NAMES[action.type] || action.type}
              {action.pai ? ` ${tileToText(action.pai)}` : ''}
            </strong>
          </span>
        )}

        {showCallBanner && (
          <div className="call-banner">
            <div
              className="call-hero"
              style={{ background: heroColor, borderColor: heroColor }}
            >
              <span className="call-hero-text">
                {CALL_NAMES[action.type] || action.type}
              </span>
              {action.pai && (
                <span className="call-hero-tile">{tileToText(action.pai)}</span>
              )}
              {action.type === 'reach' && (
                <span className="call-hero-hint">打牌见下一步</span>
              )}
            </div>
            {top3.length > 0 && (
              <div className="call-choices">
                {top3.map(([tile, prob], i) => {
                  const color = getCallColor(tile);
                  const name = CALL_NAMES[tile] || ACTION_NAMES[tile] || tileToText(tile);
                  const isTop = i === 0;
                  return (
                    <div
                      key={i}
                      className={`call-choice ${isTop ? 'call-choice-top' : ''}`}
                      style={{
                        borderColor: isTop ? color : undefined,
                        background: isTop ? color + '10' : undefined,
                      }}
                    >
                      <span className="call-choice-name" style={{ color }}>
                        {name}
                      </span>
                      <span className="call-choice-prob">
                        {(prob * 100).toFixed(1)}%
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {!showCallBanner && top3.length > 0 && (
        <div className="rec-top3">
          {top3.map(([tile, prob], i) => (
            <span key={i} className="top3-item">
              <span className="top3-rank">{['①','②','③'][i]}</span>
              <span className="top3-tile">{ACTION_NAMES[tile] || tileToText(tile)}</span>
              <span className="top3-prob">{(prob * 100).toFixed(1)}%</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
