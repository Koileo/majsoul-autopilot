import type { CSSProperties, ReactElement } from "react";
import type { MeldSnapshot, PlayerSnapshot, TableSnapshot } from "./types";

type Props = {
  table: TableSnapshot | null;
  labels: TableLabels;
};

type TableLabels = {
  self: string;
  noTable: string;
  riichi: string;
  tsumoWin: string;
  ronWin: string;
  dealIn: string;
  winningHand: string;
  exhaustiveDraw: string;
  abortiveDraw: string;
  winTile: string;
  points: string;
  han: string;
  fu: string;
  allPlayersPay: string;
  winningMelds: string;
  formatTileName: (tile: string) => string;
  topPlayer: string;
  leftPlayer: string;
  rightPlayer: string;
  dora: string;
  honba: string;
  deposit: string;
  roundEast: string;
  roundSouth: string;
  roundWest: string;
  roundNorth: string;
};

const TILE_SVG_MAP: Record<string, string> = {
  "1m": "Man1",
  "2m": "Man2",
  "3m": "Man3",
  "4m": "Man4",
  "5m": "Man5",
  "6m": "Man6",
  "7m": "Man7",
  "8m": "Man8",
  "9m": "Man9",
  "5mr": "Man5-Dora",
  "1p": "Pin1",
  "2p": "Pin2",
  "3p": "Pin3",
  "4p": "Pin4",
  "5p": "Pin5",
  "6p": "Pin6",
  "7p": "Pin7",
  "8p": "Pin8",
  "9p": "Pin9",
  "5pr": "Pin5-Dora",
  "1s": "Sou1",
  "2s": "Sou2",
  "3s": "Sou3",
  "4s": "Sou4",
  "5s": "Sou5",
  "6s": "Sou6",
  "7s": "Sou7",
  "8s": "Sou8",
  "9s": "Sou9",
  "5sr": "Sou5-Dora",
  E: "Ton",
  S: "Nan",
  W: "Shaa",
  N: "Pei",
  P: "Haku",
  F: "Hatsu",
  C: "Chun",
};

export function GameTable({ table, labels }: Props) {
  const useDemoTable =
    !table && typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "running";
  const snapshot = table ?? (useDemoTable ? demoTable() : null);
  if (!snapshot) {
    return (
      <div className="gameTable gameTableEmpty">
        <div className="emptyTablePanel">
          <strong>{labels.noTable}</strong>
        </div>
      </div>
    );
  }
  const players = normalizedPlayers(snapshot);
  const dora = snapshot.dora_markers.length > 0 ? snapshot.dora_markers : ["?"];

  return (
    <div className="gameTable">
      <div className="tableArea">
        <div className="tableCenterAbsolute">
          <div className="centerInfo">
            <strong>{roundTitle(snapshot.bakaze, snapshot.kyoku, labels)}</strong>
            <span>{`${snapshot.honba}${labels.honba}`}</span>
            <span>{`${snapshot.kyotaku}${labels.deposit}`}</span>
            <span className="centerDoraInfo">
              <b>{labels.dora}</b>
              <span className="centerDoraTiles">
                {dora.map((tile, index) => (
                  <Tile key={`${tile}-${index}`} tile={tile} small />
                ))}
              </span>
            </span>
          </div>
        </div>
        <RoundResultBanner snapshot={snapshot} labels={labels} />
        <SeatLayer
          relation="self"
          label={labels.self}
          wind={seatWindName(players.self.seat, snapshot.oya, labels)}
          player={players.self}
          actor={players.self.seat}
          isSelf
        />
        <SeatLayer
          relation="shimocha"
          label={labels.rightPlayer}
          wind={seatWindName(players.right.seat, snapshot.oya, labels)}
          player={players.right}
          actor={players.right.seat}
        />
        <SeatLayer
          relation="toimen"
          label={labels.topPlayer}
          wind={seatWindName(players.top.seat, snapshot.oya, labels)}
          player={players.top}
          actor={players.top.seat}
        />
        <SeatLayer
          relation="kamicha"
          label={labels.leftPlayer}
          wind={seatWindName(players.left.seat, snapshot.oya, labels)}
          player={players.left}
          actor={players.left.seat}
        />
      </div>
    </div>
  );
}

type SeatRelation = "self" | "shimocha" | "toimen" | "kamicha";

const SEAT_ROTATION: Record<SeatRelation, number> = {
  self: 0,
  kamicha: 90,
  toimen: 180,
  shimocha: 270,
};

function SeatLayer({
  relation,
  label,
  wind,
  player,
  actor,
  isSelf = false,
}: {
  relation: SeatRelation;
  label: string;
  wind: string;
  player: PlayerSnapshot;
  actor: number;
  isSelf?: boolean;
}) {
  return (
    <div
      className={`seatLayer seatLayer-${relation}`}
      data-seat-relation={relation}
      style={{ "--seat-rotation": `${SEAT_ROTATION[relation]}deg` } as CSSProperties}
    >
      <div className="localPlayerLabel">
        <PlayerLabel label={label} wind={wind} player={player} />
      </div>
      <LocalRiver discards={player.discards} />
      <LocalHand player={player} isSelf={isSelf} />
      {player.melds.length > 0 ? (
        <div className="localMelds">
          {player.melds.map((meld, index) => (
            <Meld key={`${meld.kind}-${index}`} meld={meld} actor={actor} small />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PlayerLabel({ label, wind, player }: { label: string; wind?: string; player: PlayerSnapshot }) {
  return (
    <div className="playerLabel">
      <span className="playerName">
        {label}
        {wind ? <span className="seatWindTag">{wind}</span> : null}
        {player.riichi ? <em className="riichiMark">立</em> : null}
      </span>
      <span className="playerScore">{player.points}点</span>
    </div>
  );
}

function LocalHand({ player, isSelf }: { player: PlayerSnapshot; isSelf: boolean }) {
  const sourceHand = isSelf
    ? player.hand.length > 0
      ? player.hand.slice()
      : Array.from({ length: Math.max(13, player.hand_count || 13) }, () => "?")
    : Array.from({ length: Math.max(0, player.hand_count) }, () => "?");
  const hasTsumo = sourceHand.length % 3 === 2;
  const mainHand = (hasTsumo ? sourceHand.slice(0, -1) : sourceHand).slice();
  if (isSelf) mainHand.sort(tileSort);
  const tsumo = hasTsumo ? sourceHand[sourceHand.length - 1] : null;
  return (
    <div className="localHand">
      <div className="localHandTiles">
          {mainHand.map((tile, index) => (
          <Tile key={`${tile}-${index}`} tile={isSelf ? tile : "?"} faceDown={!isSelf} small={!isSelf} />
          ))}
      </div>
      {tsumo ? (
        <div className="localTsumo">
          <Tile tile={isSelf ? tsumo : "?"} faceDown={!isSelf} small={!isSelf} />
        </div>
      ) : null}
    </div>
  );
}

function LocalRiver({ discards }: { discards: PlayerSnapshot["discards"] }) {
  const visibleDiscards = discards.slice(0, 24);
  if (visibleDiscards.length === 0) {
    return null;
  }
  return (
    <div className={`localRiver${visibleDiscards.length > 18 ? " localRiverOverflow" : ""}`}>
      {visibleDiscards.map((discard, index) => (
        <span
          key={`${discard.tile}-${index}`}
          className={`localRiverSlot${discard.riichi ? " localRiverRiichiSlot" : ""}`}
          style={localRiverTileStyle(index, discard.riichi)}
        >
          <Tile tile={discard.tile} small faded={discard.tsumogiri} />
        </span>
      ))}
    </div>
  );
}

function localRiverTileStyle(index: number, riichi: boolean): CSSProperties {
  const col = index % 6;
  const row = Math.floor(index / 6);
  const step = 23;
  const rowStep = 32;
  return {
    left: `${col * step}px`,
    top: `${row * rowStep}px`,
    transform: riichi ? "rotate(90deg)" : "none",
  };
}

function RoundResultBanner({ snapshot, labels }: { snapshot: TableSnapshot; labels: TableLabels }) {
  const event = snapshot.last_event;
  if (!event) return null;

  if (event.type === "hule") {
    const actor = typeof event.actor === "number" ? event.actor : null;
    const relation = actor == null ? "" : playerRelationBySeat(actor, snapshot.seat, labels);
    const zimo = Boolean(event.zimo);
    const target = typeof event.target === "number" ? event.target : null;
    const targetRelation = target == null ? "" : playerRelationBySeat(target, snapshot.seat, labels);
    const pai = typeof event.pai === "string" ? event.pai : "";
    const title = typeof event.title === "string" ? event.title : "";
    const pointSum = typeof event.point_sum === "number" && event.point_sum > 0 ? event.point_sum : null;
    const count = typeof event.count === "number" && event.count > 0 ? event.count : null;
    const fu = typeof event.fu === "number" && event.fu > 0 ? event.fu : null;
    const handTiles = huleHandTiles(event);
    const winner = actor == null ? null : snapshot.players.find((player) => player.seat === actor) ?? null;
    const winnerMelds = winner?.melds ?? [];
    const score = formatHuleScore({
      zimo,
      count,
      fu,
      pointSum,
      isDealer: actor != null && actor === snapshot.oya,
      labels,
    });
    return (
      <div className="roundResultBanner roundResultWin">
        <strong>{`${relation} ${zimo ? labels.tsumoWin : labels.ronWin}`}</strong>
        <span>{formatHuleSummary({ pai, title, score, count, fu, labels })}</span>
        {!zimo && targetRelation ? <em>{`${targetRelation}${labels.dealIn}`}</em> : null}
        {handTiles.length > 0 ? (
          <div className="winningHandReveal" aria-label={labels.winningHand}>
            <b>{labels.winningHand}</b>
            <div>
              {handTiles.map((tile, index) => (
                <Tile key={`${tile}-${index}`} tile={tile} small />
              ))}
            </div>
          </div>
        ) : null}
        {winnerMelds.length > 0 ? (
          <div className="winningMeldReveal" aria-label={labels.winningMelds}>
            <b>{labels.winningMelds}</b>
            <div>
              {winnerMelds.map((meld, index) => (
                <Meld key={`${meld.kind}-${index}`} meld={meld} actor={winner?.seat ?? actor ?? 0} small />
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  if (event.type === "no_tile") {
    return (
      <div className="roundResultBanner">
        <strong>{labels.exhaustiveDraw}</strong>
      </div>
    );
  }

  if (event.type === "liu_ju") {
    return (
      <div className="roundResultBanner">
        <strong>{labels.abortiveDraw}</strong>
      </div>
    );
  }

  return null;
}

function huleHandTiles(event: Record<string, unknown>) {
  const hand = Array.isArray(event.hand) ? event.hand.filter((tile): tile is string => typeof tile === "string") : [];
  const sortedHand = hand.slice().sort(tileSort);
  const pai = typeof event.pai === "string" && event.pai !== "?" ? event.pai : null;
  if (pai && (sortedHand.length === 0 || sortedHand.length % 3 === 1)) {
    return [...sortedHand, pai];
  }
  return sortedHand;
}

function isMeaningfulFanName(name: string) {
  const normalized = name.trim();
  if (!normalized) return false;
  return !/^\d+\s*(番|翻|han)$/i.test(normalized);
}

function formatHuleSummary({
  pai,
  title,
  score,
  count,
  fu,
  labels,
}: {
  pai: string;
  title: string;
  score: string | null;
  count: number | null;
  fu: number | null;
  labels: TableLabels;
}) {
  const parts: string[] = [];
  if (pai) parts.push(`${labels.winTile} ${labels.formatTileName(pai)}`);
  if (title && isMeaningfulFanName(title)) parts.push(title);
  if (count) parts.push(`${count}${labels.han}`);
  if (fu) parts.push(`${fu}${labels.fu}`);
  if (score) parts.push(score);
  return parts.join(" · ");
}

function formatHuleScore({
  zimo,
  count,
  fu,
  pointSum,
  isDealer,
  labels,
}: {
  zimo: boolean;
  count: number | null;
  fu: number | null;
  pointSum: number | null;
  isDealer: boolean;
  labels: TableLabels;
}) {
  if (count != null && fu != null) {
    const score = calculateHandScore({ count, fu, zimo, isDealer });
    if (score) {
      if (score.kind === "dealerTsumo") {
        return `${score.all}${labels.points}${labels.allPlayersPay}`;
      }
      if (score.kind === "childTsumo") {
        return `${score.child}${labels.points}/${score.dealer}${labels.points}`;
      }
      return `${score.ron}${labels.points}`;
    }
  }
  return pointSum != null ? `${pointSum}${labels.points}` : null;
}

function calculateHandScore({
  count,
  fu,
  zimo,
  isDealer,
}: {
  count: number;
  fu: number;
  zimo: boolean;
  isDealer: boolean;
}):
  | { kind: "dealerTsumo"; all: number }
  | { kind: "childTsumo"; child: number; dealer: number }
  | { kind: "ron"; ron: number }
  | null {
  const base = handBasePoint(count, fu);
  if (base == null) return null;
  if (zimo) {
    if (isDealer) {
      return { kind: "dealerTsumo", all: ceilHundred(base * 2) };
    }
    return { kind: "childTsumo", child: ceilHundred(base), dealer: ceilHundred(base * 2) };
  }
  return { kind: "ron", ron: ceilHundred(base * (isDealer ? 6 : 4)) };
}

function handBasePoint(count: number, fu: number) {
  if (count <= 0 || fu <= 0) return null;
  if (count >= 13) return 8000;
  if (count >= 11) return 6000;
  if (count >= 8) return 4000;
  if (count >= 6) return 3000;
  if (count >= 5 || (count === 4 && fu >= 40) || (count === 3 && fu >= 70)) return 2000;
  return fu * 2 ** (count + 2);
}

function ceilHundred(value: number) {
  return Math.ceil(value / 100) * 100;
}

function Meld({
  meld,
  actor,
  small = false,
}: {
  meld: MeldSnapshot;
  actor: number;
  small?: boolean;
}) {
  if (meld.kind === "ankan") {
    const tiles = meld.consumed.length >= 4 ? meld.consumed : [meld.consumed[0], meld.consumed[1], meld.consumed[2], meld.consumed[3]].filter(Boolean);
    return (
      <div
        className="meld meldAnkan"
        data-meld-kind="ankan"
      >
        <Tile key="hidden-left" tile="?" small={small} faceDown />
        {tiles.slice(1, 3).map((tile, index) => (
          <Tile key={`${tile}-${index + 1}`} tile={tile} small={small} />
        ))}
        <Tile key="hidden-right" tile="?" small={small} faceDown />
      </div>
    );
  }

  if (!meld.called_tile || meld.target == null) {
    return (
      <div
        className="meld"
        data-meld-kind={meld.kind}
      >
        {meld.consumed.map((tile, index) => (
          <Tile key={`${tile}-${index}`} tile={tile} small={small} />
        ))}
        {meld.called_tile ? <Tile tile={meld.called_tile} small={small} /> : null}
      </div>
    );
  }

  const relativePos = (meld.target - actor + 4) % 4;
  if (meld.kind === "kakan") {
    const baseConsumed = meld.consumed.slice(0, 2);
    while (baseConsumed.length < 2) {
      baseConsumed.push(meld.called_tile);
    }
    const addedTile = meld.consumed[2] ?? meld.consumed[0] ?? meld.called_tile;
    const calledStack = (
      <span
        key="called"
        className={`kakanCalledStack${small ? " kakanCalledStackSmall" : ""} kakanCalledFrom${relativePos}`}
        data-called-relative={relativePos}
      >
        <span className="kakanBaseTile">
          <Tile tile={meld.called_tile} small={small} />
        </span>
        <span className="kakanAddedTile">
          <Tile tile={addedTile} small={small} />
        </span>
      </span>
    );
    const tiles = meldTilesWithCalled(baseConsumed, calledStack, relativePos, small);
    return (
      <div
        className={`meld meldKakan meldFrom${relativePos}`}
        data-meld-kind={meld.kind}
        data-called-relative={relativePos}
      >
        {tiles}
      </div>
    );
  }

  const calledTile = (
    <span key="called" className={`calledTileWrapper${small ? " calledTileWrapperSmall" : ""}`} data-called-relative={relativePos}>
      <Tile tile={meld.called_tile} small={small} />
    </span>
  );
  const tiles = meldTilesWithCalled(meld.consumed, calledTile, relativePos, small);
  return (
    <div
      className={`meld meldFrom${relativePos}`}
      data-meld-kind={meld.kind}
      data-called-relative={relativePos}
    >
      {tiles}
    </div>
  );
}

function meldTilesWithCalled(
  consumed: string[],
  calledTile: ReactElement,
  relativePos: number,
  small: boolean,
) {
  const consumedTiles = consumed.map((tile, index) => (
    <Tile key={`${tile}-${index}`} tile={tile} small={small} />
  ));
  const insertIndex = calledTileIndex(relativePos, consumedTiles.length + 1);
  return [
    ...consumedTiles.slice(0, insertIndex),
    calledTile,
    ...consumedTiles.slice(insertIndex),
  ];
}

function calledTileIndex(relativePos: number, totalTileCount: number) {
  const middle = Math.floor((totalTileCount - 1) / 2);
  if (relativePos === 3) return 0;
  if (relativePos === 1) return Math.max(0, totalTileCount - 1);
  return middle;
}

function playerRelationBySeat(seat: number, selfSeat: number, labels: TableLabels) {
  const relative = (seat - selfSeat + 4) % 4;
  if (relative === 0) return labels.self;
  if (relative === 1) return labels.rightPlayer;
  if (relative === 2) return labels.topPlayer;
  return labels.leftPlayer;
}

function seatWindName(seat: number, oya: number, labels: TableLabels) {
  const winds = [labels.roundEast, labels.roundSouth, labels.roundWest, labels.roundNorth];
  return winds[(seat - oya + 4) % 4] ?? "";
}

function Tile({
  tile,
  small = false,
  faceDown = false,
  sideways = false,
  faded = false,
  calledFrom,
}: {
  tile: string;
  small?: boolean;
  faceDown?: boolean;
  sideways?: boolean;
  faded?: boolean;
  calledFrom?: number;
}) {
  const isBack = faceDown || !tile || tile === "?";
  const src = isBack ? null : tileSvgPath(tile);
  const classes = [
    "tile",
    small ? "tileSmall" : "",
    sideways ? "tileSideways" : "",
    faded ? "tileFaded" : "",
    isBack ? "tileBack" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes} title={tile} data-called-relative={calledFrom ?? undefined}>
      {src ? <img src={src} alt={tile || "?"} draggable={false} /> : <span className="tileBackInner" />}
    </span>
  );
}

function tileSvgPath(tile: string) {
  const normalized = tile.replace(/^0([mps])$/, "5$1r").replace(/r$/, "r");
  const name = TILE_SVG_MAP[normalized] ?? TILE_SVG_MAP[tile];
  return name ? `tiles/${name}.svg` : "tiles/Back.svg";
}

function tileSort(a: string, b: string) {
  const aInfo = parseTileForSort(a);
  const bInfo = parseTileForSort(b);
  if (aInfo.suitIdx !== bInfo.suitIdx) return aInfo.suitIdx - bInfo.suitIdx;
  return aInfo.num - bInfo.num;
}

function parseTileForSort(tile: string) {
  const suitOrder: Record<string, number> = { m: 0, p: 1, s: 2 };
  const honorOrder: Record<string, number> = { E: 30, S: 31, W: 32, N: 33, P: 34, F: 35, C: 36 };
  if (honorOrder[tile] != null) {
    return { suitIdx: 3, num: honorOrder[tile] };
  }
  const zeroRed = /^0[mps]$/.test(tile);
  const isRed = tile.endsWith("r") || zeroRed;
  const base = zeroRed ? `5${tile[1]}` : isRed ? tile.slice(0, -1) : tile;
  const number = Number.parseInt(base[0] ?? "0", 10);
  const suit = base[1] ?? "";
  return { suitIdx: suitOrder[suit] ?? 4, num: isRed ? number + 0.5 : number };
}

function normalizedPlayers(table: TableSnapshot) {
  const bySeat = (seat: number) => table.players.find((player) => player.seat === seat) ?? table.players[0];
  const self = bySeat(table.seat);
  return {
    self,
    right: bySeat((table.seat + 1) % 4),
    top: bySeat((table.seat + 2) % 4),
    left: bySeat((table.seat + 3) % 4),
  };
}

function windName(wind: string, labels: TableLabels) {
  return (
    {
      E: labels.roundEast,
      S: labels.roundSouth,
      W: labels.roundWest,
      N: labels.roundNorth,
    }[wind] ?? wind
  );
}

function roundTitle(wind: string, kyoku: number, labels: TableLabels) {
  const windLabel = windName(wind, labels);
  if (/^[A-Za-z]/.test(windLabel)) return `${windLabel} ${kyoku}`;
  const cjkNumbers = ["", "一", "二", "三", "四"];
  return `${windLabel}${cjkNumbers[kyoku] ?? kyoku}局`;
}

function demoTable(): TableSnapshot {
  const players = [0, 1, 2, 3].map((seat) => ({
    seat,
    points: [24000, 16000, 25000, 35000][seat],
    hand: seat === 0 ? ["6m", "6m", "7m", "8m", "8p", "9p", "2s", "3s", "4s", "E", "S", "P", "7p", "3s"] : [],
    hand_count: seat === 0 ? 14 : 13,
    discards: ["1m", "7p", "9m", "5s", "E", "C"].slice(0, seat + 3).map((tile, index) => ({
      tile,
      tsumogiri: index % 2 === 0,
      riichi: index === 2 && seat === 3,
    })),
    melds:
      seat === 0
        ? [{ kind: "chi" as const, target: 1, called_tile: "6s", consumed: ["4s", "5sr"] }]
        : seat === 3
          ? [{ kind: "pon" as const, target: 2, called_tile: "C", consumed: ["C", "C"] }]
          : [],
    riichi: seat === 3,
    is_self: seat === 0,
  }));
  return {
    seat: 0,
    bakaze: "E",
    kyoku: 2,
    honba: 0,
    kyotaku: 1,
    oya: 1,
    dora_markers: ["4p"],
    scores: players.map((player) => player.points),
    players,
    last_event: { type: "dahai", actor: 0, pai: "3s" },
  };
}
