use mjai::bridge;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct TableSnapshot {
    pub seat: u32,
    pub bakaze: String,
    pub kyoku: u32,
    pub honba: u32,
    pub kyotaku: u32,
    pub oya: u32,
    pub dora_markers: Vec<String>,
    pub scores: Vec<i32>,
    pub players: Vec<PlayerSnapshot>,
    pub last_event: Option<bridge::Event>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PlayerSnapshot {
    pub seat: u32,
    pub points: i32,
    pub hand: Vec<String>,
    pub hand_count: usize,
    pub discards: Vec<DiscardSnapshot>,
    pub melds: Vec<MeldSnapshot>,
    pub riichi: bool,
    pub is_self: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiscardSnapshot {
    pub tile: String,
    pub tsumogiri: bool,
    pub riichi: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct MeldSnapshot {
    pub kind: MeldKind,
    pub target: Option<u32>,
    pub called_tile: Option<String>,
    pub consumed: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MeldKind {
    Chi,
    Pon,
    Daiminkan,
    Ankan,
    Kakan,
}

#[derive(Debug, Clone)]
pub struct TableTracker {
    snapshot: TableSnapshot,
    pending_riichi_discard: Option<u32>,
    pending_kan_draw: [u8; 4],
}

impl TableTracker {
    pub fn new(seat: u32) -> Self {
        Self {
            snapshot: TableSnapshot {
                seat,
                bakaze: "E".to_string(),
                kyoku: 1,
                honba: 0,
                kyotaku: 0,
                oya: 0,
                dora_markers: Vec::new(),
                scores: vec![25_000; 4],
                players: (0..4)
                    .map(|idx| PlayerSnapshot {
                        seat: idx,
                        points: 25_000,
                        hand: Vec::new(),
                        hand_count: 13,
                        discards: Vec::new(),
                        melds: Vec::new(),
                        riichi: false,
                        is_self: idx == seat,
                    })
                    .collect(),
                last_event: None,
            },
            pending_riichi_discard: None,
            pending_kan_draw: [0; 4],
        }
    }

    pub fn snapshot(&self) -> TableSnapshot {
        self.snapshot.clone()
    }

    pub fn apply(&mut self, event: &bridge::Event) -> TableSnapshot {
        match event {
            bridge::Event::StartKyoku {
                bakaze,
                dora_marker,
                honba,
                kyoku,
                kyotaku,
                oya,
                scores,
                tehais,
            } => {
                self.snapshot.bakaze = bakaze.clone();
                self.snapshot.kyoku = *kyoku;
                self.snapshot.honba = *honba;
                self.snapshot.kyotaku = *kyotaku;
                self.snapshot.oya = *oya;
                self.snapshot.dora_markers = vec![dora_marker.clone()];
                self.snapshot.scores = scores.clone();
                self.pending_riichi_discard = None;
                self.pending_kan_draw = [0; 4];
                for idx in 0..4 {
                    let player = &mut self.snapshot.players[idx];
                    player.points = *scores.get(idx).unwrap_or(&25_000);
                    player.discards.clear();
                    player.melds.clear();
                    player.riichi = false;
                    player.hand = tehais.get(idx).cloned().unwrap_or_default();
                    player.hand_count = player.hand.len();
                }
            }
            bridge::Event::Tsumo { actor, pai } => {
                let pending_kan_draw = self.consume_pending_kan_draw(*actor);
                if let Some(player) = self.player_mut(*actor) {
                    if !pending_kan_draw {
                        player.hand_count += 1;
                    }
                    if player.is_self {
                        player.hand.push(pai.clone());
                    }
                }
            }
            bridge::Event::Reach { actor } => {
                self.pending_riichi_discard = Some(*actor);
            }
            bridge::Event::ReachAccepted { actor } => {
                if let Some(player) = self.player_mut(*actor) {
                    player.riichi = true;
                }
            }
            bridge::Event::Dahai {
                actor,
                pai,
                tsumogiri,
            } => {
                let riichi = self.pending_riichi_discard.take() == Some(*actor);
                if let Some(player) = self.player_mut(*actor) {
                    player.hand_count = player.hand_count.saturating_sub(1);
                    if player.is_self {
                        remove_one_tile(&mut player.hand, pai);
                    }
                    player.discards.push(DiscardSnapshot {
                        tile: pai.clone(),
                        tsumogiri: *tsumogiri,
                        riichi,
                    });
                }
            }
            bridge::Event::Chi {
                actor,
                target,
                pai,
                consumed,
            } => self.add_meld(
                *actor,
                MeldSnapshot {
                    kind: MeldKind::Chi,
                    target: Some(*target),
                    called_tile: Some(pai.clone()),
                    consumed: consumed.clone(),
                },
            ),
            bridge::Event::Pon {
                actor,
                target,
                pai,
                consumed,
            } => self.add_meld(
                *actor,
                MeldSnapshot {
                    kind: MeldKind::Pon,
                    target: Some(*target),
                    called_tile: Some(pai.clone()),
                    consumed: consumed.clone(),
                },
            ),
            bridge::Event::Daiminkan {
                actor,
                target,
                pai,
                consumed,
            } => {
                self.add_meld(
                    *actor,
                    MeldSnapshot {
                        kind: MeldKind::Daiminkan,
                        target: Some(*target),
                        called_tile: Some(pai.clone()),
                        consumed: consumed.clone(),
                    },
                );
                self.mark_pending_kan_draw(*actor);
            }
            bridge::Event::Ankan { actor, consumed } => {
                self.add_meld(
                    *actor,
                    MeldSnapshot {
                        kind: MeldKind::Ankan,
                        target: None,
                        called_tile: None,
                        consumed: consumed.clone(),
                    },
                );
                self.mark_pending_kan_draw(*actor);
            }
            bridge::Event::Kakan {
                actor,
                pai,
                consumed,
            } => self.upgrade_kakan(*actor, pai, consumed),
            bridge::Event::Dora { markers } => {
                self.snapshot.dora_markers = markers.clone();
            }
            bridge::Event::Hule { .. } | bridge::Event::NoTile { .. } | bridge::Event::LiuJu { .. } => {}
            bridge::Event::EndKyoku | bridge::Event::EndGame | bridge::Event::StartGame { .. } => {}
        }
        if !matches!(event, bridge::Event::EndKyoku)
            || !matches!(
                self.snapshot.last_event,
                Some(bridge::Event::Hule { .. } | bridge::Event::NoTile { .. } | bridge::Event::LiuJu { .. })
            )
        {
            self.snapshot.last_event = Some(event.clone());
        }
        self.snapshot.clone()
    }

    fn mark_pending_kan_draw(&mut self, actor: u32) {
        if let Some(player) = self.player_mut(actor) {
            player.hand_count += 1;
        }
        if let Some(pending) = self.pending_kan_draw.get_mut(actor as usize) {
            *pending = pending.saturating_add(1);
        }
    }

    fn consume_pending_kan_draw(&mut self, actor: u32) -> bool {
        let Some(pending) = self.pending_kan_draw.get_mut(actor as usize) else {
            return false;
        };
        if *pending == 0 {
            return false;
        }
        *pending -= 1;
        true
    }

    fn add_meld(&mut self, actor: u32, meld: MeldSnapshot) {
        if let (Some(target), Some(called_tile)) = (meld.target, meld.called_tile.as_deref()) {
            self.remove_called_discard(target, called_tile);
        }
        if let Some(player) = self.player_mut(actor) {
            player.hand_count = player.hand_count.saturating_sub(meld.consumed.len());
            if player.is_self {
                for tile in &meld.consumed {
                    remove_one_tile(&mut player.hand, tile);
                }
            }
            player.melds.push(meld);
        }
    }

    fn upgrade_kakan(&mut self, actor: u32, pai: &str, consumed: &[String]) {
        let mut upgraded = false;
        if let Some(player) = self.player_mut(actor) {
            if let Some(index) = player
                .melds
                .iter()
                .rposition(|meld| meld.kind == MeldKind::Pon && meld_matches_tile(meld, pai))
            {
                player.melds[index].kind = MeldKind::Kakan;
                player.melds[index].consumed = consumed.to_vec();
                player.hand_count = player.hand_count.saturating_sub(1);
                if player.is_self {
                    remove_one_equivalent_tile(&mut player.hand, pai);
                }
                upgraded = true;
            }
        }
        if upgraded {
            self.mark_pending_kan_draw(actor);
            return;
        }

        if let Some(player) = self.player_mut(actor) {
            player.hand_count = player.hand_count.saturating_sub(1);
            if player.is_self {
                remove_one_equivalent_tile(&mut player.hand, pai);
            }
            player.melds.push(MeldSnapshot {
                kind: MeldKind::Kakan,
                target: None,
                called_tile: Some(pai.to_string()),
                consumed: consumed.to_vec(),
            });
        }
        self.mark_pending_kan_draw(actor);
    }

    fn remove_called_discard(&mut self, target: u32, tile: &str) {
        let mut removed_riichi_discard = false;
        {
            let Some(player) = self.player_mut(target) else {
                return;
            };
            if player
                .discards
                .last()
                .is_some_and(|discard| tile_without_red(&discard.tile) == tile_without_red(tile))
            {
                removed_riichi_discard = player.discards.pop().is_some_and(|discard| discard.riichi);
            } else if let Some(index) = player
                .discards
                .iter()
                .rposition(|discard| tile_without_red(&discard.tile) == tile_without_red(tile))
            {
                removed_riichi_discard = player.discards.remove(index).riichi;
            }
        }
        if removed_riichi_discard {
            self.pending_riichi_discard = Some(target);
        }
    }

    fn player_mut(&mut self, seat: u32) -> Option<&mut PlayerSnapshot> {
        self.snapshot.players.get_mut(seat as usize)
    }
}

fn remove_one_tile(hand: &mut Vec<String>, tile: &str) {
    if let Some(idx) = hand.iter().position(|item| item == tile) {
        hand.remove(idx);
    }
}

fn remove_one_equivalent_tile(hand: &mut Vec<String>, tile: &str) {
    if let Some(idx) = hand.iter().position(|item| item == tile) {
        hand.remove(idx);
    } else if let Some(idx) = hand
        .iter()
        .position(|item| tile_without_red(item) == tile_without_red(tile))
    {
        hand.remove(idx);
    }
}

fn meld_matches_tile(meld: &MeldSnapshot, tile: &str) -> bool {
    meld.called_tile
        .as_deref()
        .is_some_and(|called| tile_without_red(called) == tile_without_red(tile))
        || meld
            .consumed
            .iter()
            .any(|consumed| tile_without_red(consumed) == tile_without_red(tile))
}

fn tile_without_red(tile: &str) -> &str {
    tile.strip_suffix('r').unwrap_or(tile)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn start_event() -> bridge::Event {
        bridge::Event::StartKyoku {
            bakaze: "E".to_string(),
            dora_marker: "4p".to_string(),
            honba: 0,
            kyoku: 1,
            kyotaku: 0,
            oya: 0,
            scores: vec![25_000; 4],
            tehais: vec![
                vec![
                    "1m".to_string(),
                    "2m".to_string(),
                    "3m".to_string(),
                    "4p".to_string(),
                    "4p".to_string(),
                    "4p".to_string(),
                    "5s".to_string(),
                    "5s".to_string(),
                    "5s".to_string(),
                    "E".to_string(),
                    "E".to_string(),
                    "P".to_string(),
                    "P".to_string(),
                ],
                vec![],
                vec![],
                vec![],
            ],
        }
    }

    #[test]
    fn reach_discard_is_marked_in_river() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());
        tracker.apply(&bridge::Event::Reach { actor: 0 });
        let snapshot = tracker.apply(&bridge::Event::Dahai {
            actor: 0,
            pai: "P".to_string(),
            tsumogiri: false,
        });
        let discard = snapshot.players[0].discards.last().unwrap();
        assert!(discard.riichi);
        assert_eq!(discard.tile, "P");
    }

    #[test]
    fn called_riichi_discard_moves_marker_to_next_discard() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());
        tracker.apply(&bridge::Event::Reach { actor: 0 });
        tracker.apply(&bridge::Event::Dahai {
            actor: 0,
            pai: "4p".to_string(),
            tsumogiri: false,
        });
        tracker.apply(&bridge::Event::Pon {
            actor: 1,
            target: 0,
            pai: "4p".to_string(),
            consumed: vec!["4p".to_string(), "4p".to_string()],
        });

        let snapshot = tracker.apply(&bridge::Event::Dahai {
            actor: 0,
            pai: "P".to_string(),
            tsumogiri: false,
        });

        assert_eq!(
            snapshot.players[0]
                .discards
                .iter()
                .map(|discard| (discard.tile.as_str(), discard.riichi))
                .collect::<Vec<_>>(),
            vec![("P", true)]
        );
    }

    #[test]
    fn melds_are_tracked_and_remove_self_consumed_tiles() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());
        let snapshot = tracker.apply(&bridge::Event::Pon {
            actor: 0,
            target: 2,
            pai: "4p".to_string(),
            consumed: vec!["4p".to_string(), "4p".to_string()],
        });
        assert_eq!(snapshot.players[0].melds.len(), 1);
        assert_eq!(snapshot.players[0].melds[0].kind, MeldKind::Pon);
        assert_eq!(snapshot.players[0].hand_count, 11);
        assert_eq!(
            snapshot.players[0]
                .hand
                .iter()
                .filter(|tile| tile.as_str() == "4p")
                .count(),
            1
        );
    }

    #[test]
    fn kakan_upgrades_existing_pon_in_place() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());
        tracker.apply(&bridge::Event::Pon {
            actor: 0,
            target: 2,
            pai: "4p".to_string(),
            consumed: vec!["4p".to_string(), "4p".to_string()],
        });

        let snapshot = tracker.apply(&bridge::Event::Kakan {
            actor: 0,
            pai: "4p".to_string(),
            consumed: vec!["4p".to_string(), "4p".to_string(), "4p".to_string()],
        });

        assert_eq!(snapshot.players[0].melds.len(), 1);
        assert_eq!(snapshot.players[0].melds[0].kind, MeldKind::Kakan);
        assert_eq!(snapshot.players[0].melds[0].target, Some(2));
        assert_eq!(
            snapshot.players[0].melds[0].called_tile.as_deref(),
            Some("4p")
        );
        assert_eq!(snapshot.players[0].melds[0].consumed.len(), 3);
        assert_eq!(snapshot.players[0].hand_count, 11);
        assert_eq!(
            snapshot.players[0]
                .hand
                .iter()
                .filter(|tile| tile.as_str() == "4p")
                .count(),
            0
        );
    }

    #[test]
    fn daiminkan_immediately_displays_rinshan_draw_count() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&bridge::Event::StartKyoku {
            bakaze: "E".to_string(),
            dora_marker: "4p".to_string(),
            honba: 0,
            kyoku: 1,
            kyotaku: 0,
            oya: 0,
            scores: vec![25_000; 4],
            tehais: vec![
                vec!["?".to_string(); 13],
                vec!["?".to_string(); 6],
                vec!["?".to_string(); 13],
                vec!["?".to_string(); 13],
            ],
        });

        let snapshot = tracker.apply(&bridge::Event::Daiminkan {
            actor: 1,
            target: 2,
            pai: "3s".to_string(),
            consumed: vec!["3s".to_string(), "3s".to_string(), "3s".to_string()],
        });

        assert_eq!(snapshot.players[1].hand_count, 4);
    }

    #[test]
    fn rinshan_deal_after_kan_does_not_double_count() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&bridge::Event::StartKyoku {
            bakaze: "E".to_string(),
            dora_marker: "4p".to_string(),
            honba: 0,
            kyoku: 1,
            kyotaku: 0,
            oya: 0,
            scores: vec![25_000; 4],
            tehais: vec![
                vec!["?".to_string(); 13],
                vec!["?".to_string(); 6],
                vec!["?".to_string(); 13],
                vec!["?".to_string(); 13],
            ],
        });
        tracker.apply(&bridge::Event::Daiminkan {
            actor: 1,
            target: 2,
            pai: "3s".to_string(),
            consumed: vec!["3s".to_string(), "3s".to_string(), "3s".to_string()],
        });

        let snapshot = tracker.apply(&bridge::Event::Tsumo {
            actor: 1,
            pai: "?".to_string(),
        });

        assert_eq!(snapshot.players[1].hand_count, 4);
    }

    #[test]
    fn open_meld_removes_called_tile_from_target_river() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());
        tracker.apply(&bridge::Event::Dahai {
            actor: 2,
            pai: "3s".to_string(),
            tsumogiri: false,
        });
        tracker.apply(&bridge::Event::Dahai {
            actor: 2,
            pai: "4s".to_string(),
            tsumogiri: false,
        });

        let snapshot = tracker.apply(&bridge::Event::Daiminkan {
            actor: 1,
            target: 2,
            pai: "4s".to_string(),
            consumed: vec!["4s".to_string(), "4s".to_string(), "4s".to_string()],
        });

        assert_eq!(
            snapshot.players[2]
                .discards
                .iter()
                .map(|discard| discard.tile.as_str())
                .collect::<Vec<_>>(),
            vec!["3s"]
        );
        assert_eq!(snapshot.players[1].melds[0].target, Some(2));
        assert_eq!(snapshot.players[1].melds[0].called_tile.as_deref(), Some("4s"));
    }

    #[test]
    fn dora_event_updates_dora_markers() {
        let mut tracker = TableTracker::new(0);
        tracker.apply(&start_event());

        let snapshot = tracker.apply(&bridge::Event::Dora {
            markers: vec!["4p".to_string(), "5sr".to_string()],
        });

        assert_eq!(snapshot.dora_markers, vec!["4p", "5sr"]);
    }
}
