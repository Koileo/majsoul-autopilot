use anyhow::{bail, Result};
use liqi::pb;
use prost::Message;
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Event {
    StartGame {
        id: u32,
    },
    StartKyoku {
        bakaze: String,
        dora_marker: String,
        honba: u32,
        kyoku: u32,
        kyotaku: u32,
        oya: u32,
        scores: Vec<i32>,
        tehais: Vec<Vec<String>>,
    },
    Tsumo {
        actor: u32,
        pai: String,
    },
    Reach {
        actor: u32,
    },
    ReachAccepted {
        actor: u32,
    },
    Dahai {
        actor: u32,
        pai: String,
        tsumogiri: bool,
    },
    Chi {
        actor: u32,
        target: u32,
        pai: String,
        consumed: Vec<String>,
    },
    Pon {
        actor: u32,
        target: u32,
        pai: String,
        consumed: Vec<String>,
    },
    Daiminkan {
        actor: u32,
        target: u32,
        pai: String,
        consumed: Vec<String>,
    },
    Ankan {
        actor: u32,
        consumed: Vec<String>,
    },
    Kakan {
        actor: u32,
        pai: String,
        consumed: Vec<String>,
    },
    EndKyoku,
    EndGame,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationContext {
    pub source: String,
    pub seat: u32,
    pub received_key: u64,
    pub received_at: Instant,
    pub time_add: u32,
    pub time_fixed: u32,
    pub passed_waiting_time: u32,
}

#[derive(Debug, Default)]
pub struct Bridge {
    seat: u32,
    doras: Vec<String>,
    my_tehais: Vec<String>,
    my_tsumohai: Option<String>,
    accept_reach: Option<Event>,
    last_operation_list: Vec<pb::OptionalOperation>,
    last_operation_context: Option<OperationContext>,
    received_key: u64,
    discard_counter: u64,
    round_end_counter: u64,
}

impl Bridge {
    pub fn new(seat: u32) -> Self {
        Self {
            seat,
            ..Default::default()
        }
    }

    pub fn last_operation_list(&self) -> &[pb::OptionalOperation] {
        &self.last_operation_list
    }

    pub fn last_operation_context(&self) -> Option<&OperationContext> {
        self.last_operation_context.as_ref()
    }

    pub fn discard_counter(&self) -> u64 {
        self.discard_counter
    }

    pub fn round_end_counter(&self) -> u64 {
        self.round_end_counter
    }

    pub fn seat(&self) -> u32 {
        self.seat
    }

    pub fn my_tehais(&self) -> &[String] {
        &self.my_tehais
    }

    pub fn my_tsumohai(&self) -> Option<&str> {
        self.my_tsumohai.as_deref()
    }

    pub fn handle_action(&mut self, name: &str, data: &[u8]) -> Result<Vec<Event>> {
        self.handle_action_with_waiting(name, data, 0)
    }

    pub fn handle_action_with_waiting(
        &mut self,
        name: &str,
        data: &[u8],
        passed_waiting_time: u32,
    ) -> Result<Vec<Event>> {
        self.received_key += 1;
        let mut events = Vec::new();
        if let Some(event) = self.accept_reach.take() {
            events.push(event);
        }

        match name {
            "ActionNewRound" => {
                let action = pb::ActionNewRound::decode(data)?;
                self.all_ready_new_round(&action, &mut events, passed_waiting_time);
            }
            "ActionDealTile" => {
                let action = pb::ActionDealTile::decode(data)?;
                if let Some(operation) = action.operation.clone() {
                    self.set_operation("ActionDealTile", operation, passed_waiting_time);
                } else {
                    self.clear_operation();
                }
                let pai = if action.tile.is_empty() {
                    "?".to_string()
                } else {
                    ms_tile_to_mjai(&action.tile)
                };
                if action.seat == self.seat {
                    self.my_tsumohai = Some(pai.clone());
                }
                events.push(Event::Tsumo {
                    actor: action.seat,
                    pai,
                });
                self.update_dora_events(&action.doras);
            }
            "ActionDiscardTile" => {
                let action = pb::ActionDiscardTile::decode(data)?;
                if let Some(operation) = action.operation.clone() {
                    self.set_operation("ActionDiscardTile", operation, passed_waiting_time);
                } else {
                    self.clear_operation();
                }
                self.discard_counter += 1;
                if action.is_liqi {
                    events.push(Event::Reach { actor: action.seat });
                }
                let pai = ms_tile_to_mjai(&action.tile);
                if action.seat == self.seat {
                    self.apply_self_discard(&pai, action.moqie);
                }
                events.push(Event::Dahai {
                    actor: action.seat,
                    pai,
                    tsumogiri: action.moqie,
                });
                if action.is_liqi {
                    self.accept_reach = Some(Event::ReachAccepted { actor: action.seat });
                }
                self.update_dora_events(&action.doras);
            }
            "ActionChiPengGang" => {
                let action = pb::ActionChiPengGang::decode(data)?;
                if let Some(operation) = action.operation.clone() {
                    self.set_operation("ActionChiPengGang", operation, passed_waiting_time);
                } else {
                    self.clear_operation();
                }
                events.push(chi_peng_gang_event(&action)?);
            }
            "ActionAnGangAddGang" => {
                let action = pb::ActionAnGangAddGang::decode(data)?;
                if let Some(operation) = action.operation.clone() {
                    self.set_operation("ActionAnGangAddGang", operation, passed_waiting_time);
                } else {
                    self.clear_operation();
                }
                events.push(an_gang_add_gang_event(&action)?);
                self.update_dora_events(&action.doras);
            }
            "ActionHule" | "ActionNoTile" | "ActionLiuJu" => {
                self.round_end_counter += 1;
                self.clear_operation();
                events.push(Event::EndKyoku);
            }
            _ => {}
        }

        Ok(events)
    }

    fn all_ready_new_round(
        &mut self,
        action: &pb::ActionNewRound,
        events: &mut Vec<Event>,
        passed_waiting_time: u32,
    ) {
        let bakaze = ["E", "S", "W", "N"]
            .get(action.chang as usize)
            .unwrap_or(&"?")
            .to_string();
        let dora_marker = action
            .doras
            .first()
            .or_else(|| (!action.dora.is_empty()).then_some(&action.dora))
            .map(|tile| ms_tile_to_mjai(tile))
            .unwrap_or_else(|| "?".to_string());
        self.doras = if action.doras.is_empty() {
            vec![action.dora.clone()]
        } else {
            action.doras.clone()
        };

        let mut tehais = vec![vec!["?".to_string(); 13]; 4];
        let hand_len = action.tiles.len().min(13);
        let mut my_tehais = action.tiles[..hand_len]
            .iter()
            .map(|tile| ms_tile_to_mjai(tile))
            .collect::<Vec<_>>();
        my_tehais.sort_by_key(|tile| tile_sort_key(tile));
        self.my_tehais = my_tehais.clone();
        self.my_tsumohai = None;
        tehais[self.seat as usize] = my_tehais;

        events.push(Event::StartKyoku {
            bakaze,
            dora_marker,
            honba: action.ben,
            kyoku: action.ju + 1,
            kyotaku: action.liqibang,
            oya: action.ju,
            scores: action.scores.clone(),
            tehais,
        });

        if action.tiles.len() == 14 {
            let pai = ms_tile_to_mjai(&action.tiles[13]);
            self.my_tsumohai = Some(pai.clone());
            events.push(Event::Tsumo { actor: self.seat, pai });
        }

        if let Some(operation) = action.operation.clone() {
            self.set_operation("ActionNewRound", operation, passed_waiting_time);
        } else {
            self.clear_operation();
        }
    }

    fn set_operation(
        &mut self,
        source: &str,
        operation: pb::OptionalOperationList,
        passed_waiting_time: u32,
    ) {
        self.last_operation_list = operation.operation_list;
        self.last_operation_context = Some(OperationContext {
            source: source.to_string(),
            seat: operation.seat,
            received_key: self.received_key,
            received_at: Instant::now(),
            time_add: operation.time_add,
            time_fixed: operation.time_fixed,
            passed_waiting_time,
        });
    }

    fn clear_operation(&mut self) {
        self.last_operation_list.clear();
        self.last_operation_context = None;
    }

    fn update_dora_events(&mut self, doras: &[String]) {
        if doras.len() > self.doras.len() {
            self.doras = doras.to_vec();
        }
    }

    fn apply_self_discard(&mut self, pai: &str, tsumogiri: bool) {
        if tsumogiri {
            self.my_tsumohai = None;
            return;
        }

        if let Some(pos) = self.my_tehais.iter().position(|tile| tile == pai) {
            self.my_tehais.remove(pos);
        }
        if let Some(tsumo) = self.my_tsumohai.take() {
            self.my_tehais.push(tsumo);
            self.my_tehais.sort_by_key(|tile| tile_sort_key(tile));
        }
    }
}

fn chi_peng_gang_event(action: &pb::ActionChiPengGang) -> Result<Event> {
    let actor = action.seat;
    let mut target = None;
    let mut pai = None;
    let mut consumed = Vec::new();

    for (idx, from) in action.froms.iter().copied().enumerate() {
        let Some(tile) = action.tiles.get(idx) else {
            bail!("ActionChiPengGang froms/tiles length mismatch");
        };
        if from != actor {
            target = Some(from);
            pai = Some(ms_tile_to_mjai(tile));
        } else {
            consumed.push(ms_tile_to_mjai(tile));
        }
    }

    let Some(target) = target else {
        bail!("ActionChiPengGang has no target seat");
    };
    let Some(pai) = pai else {
        bail!("ActionChiPengGang has no target tile");
    };

    match action.r#type {
        0 => {
            if consumed.len() != 2 {
                bail!("chi consumed tile count is {}", consumed.len());
            }
            Ok(Event::Chi {
                actor,
                target,
                pai,
                consumed,
            })
        }
        1 => {
            if consumed.len() != 2 {
                bail!("pon consumed tile count is {}", consumed.len());
            }
            Ok(Event::Pon {
                actor,
                target,
                pai,
                consumed,
            })
        }
        2 => {
            if consumed.len() != 3 {
                bail!("daiminkan consumed tile count is {}", consumed.len());
            }
            Ok(Event::Daiminkan {
                actor,
                target,
                pai,
                consumed,
            })
        }
        other => bail!("unknown ActionChiPengGang type {other}"),
    }
}

fn an_gang_add_gang_event(action: &pb::ActionAnGangAddGang) -> Result<Event> {
    let actor = action.seat;
    let pai = ms_tile_to_mjai(&action.tiles);
    match action.r#type {
        3 => Ok(Event::Ankan {
            actor,
            consumed: repeated_kan_tiles(&pai, 4, true),
        }),
        2 => Ok(Event::Kakan {
            actor,
            pai: pai.clone(),
            consumed: repeated_kan_tiles(&pai, 3, !pai.ends_with('r')),
        }),
        other => bail!("unknown ActionAnGangAddGang type {other}"),
    }
}

fn repeated_kan_tiles(pai: &str, count: usize, force_red_five: bool) -> Vec<String> {
    let base = pai.replace('r', "");
    let mut consumed = vec![base; count];
    if force_red_five && is_five_suit_tile(pai) {
        consumed[0].push('r');
    }
    consumed
}

fn is_five_suit_tile(tile: &str) -> bool {
    tile.starts_with('5') && matches!(tile.as_bytes().get(1), Some(b'm' | b'p' | b's'))
}

pub fn ms_tile_to_mjai(tile: &str) -> String {
    match tile {
        "0m" => "5mr",
        "0p" => "5pr",
        "0s" => "5sr",
        "1z" => "E",
        "2z" => "S",
        "3z" => "W",
        "4z" => "N",
        "5z" => "P",
        "6z" => "F",
        "7z" => "C",
        other => other,
    }
    .to_string()
}

fn tile_sort_key(tile: &str) -> u32 {
    match tile {
        "E" => 31,
        "S" => 32,
        "W" => 33,
        "N" => 34,
        "P" => 35,
        "F" => 36,
        "C" => 37,
        _ => {
            let bytes = tile.as_bytes();
            if bytes.len() < 2 {
                return 99;
            }
            let number = if bytes[0] == b'5' && tile.ends_with('r') {
                5
            } else {
                (bytes[0].saturating_sub(b'0')) as u32
            };
            let suit = match bytes[1] {
                b'm' => 0,
                b'p' => 10,
                b's' => 20,
                _ => 90,
            };
            suit + number
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use prost::Message;

    fn encode<M: Message>(message: M) -> Vec<u8> {
        let mut out = Vec::new();
        message.encode(&mut out).unwrap();
        out
    }

    #[test]
    fn new_round_emits_start_kyoku_and_tsumo_for_14_tile_restore() {
        let mut bridge = Bridge::new(0);
        let data = encode(pb::ActionNewRound {
            chang: 0,
            ju: 2,
            ben: 1,
            tiles: vec![
                "1m", "2m", "3m", "4m", "0m", "6m", "7m", "8m", "9m", "1z", "2z", "3z", "4z", "5z",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
            doras: vec!["4p".to_string()],
            scores: vec![25000, 25000, 25000, 25000],
            liqibang: 0,
            operation: Some(pb::OptionalOperationList {
                seat: 0,
                operation_list: vec![pb::OptionalOperation {
                    r#type: 1,
                    combination: vec![],
                    ..Default::default()
                }],
                time_add: 5,
                time_fixed: 3,
            }),
            ..Default::default()
        });

        let events = bridge.handle_action("ActionNewRound", &data).unwrap();
        assert_eq!(events.len(), 2);
        assert!(matches!(
            events[0],
            Event::StartKyoku {
                kyoku: 3,
                honba: 1,
                ..
            }
        ));
        assert_eq!(
            events[1],
            Event::Tsumo {
                actor: 0,
                pai: "P".to_string()
            }
        );
        assert_eq!(bridge.last_operation_list()[0].r#type, 1);
        assert_eq!(
            bridge.last_operation_context().unwrap().source,
            "ActionNewRound"
        );
    }

    #[test]
    fn discard_liqi_emits_reach_dahai_and_delayed_reach_accepted() {
        let mut bridge = Bridge::new(0);
        let discard = encode(pb::ActionDiscardTile {
            seat: 1,
            tile: "0s".to_string(),
            is_liqi: true,
            moqie: true,
            ..Default::default()
        });

        let events = bridge.handle_action("ActionDiscardTile", &discard).unwrap();
        assert_eq!(
            events,
            vec![
                Event::Reach { actor: 1 },
                Event::Dahai {
                    actor: 1,
                    pai: "5sr".to_string(),
                    tsumogiri: true
                }
            ]
        );
        assert_eq!(bridge.discard_counter(), 1);

        let deal = encode(pb::ActionDealTile {
            seat: 2,
            tile: "".to_string(),
            ..Default::default()
        });
        let events = bridge.handle_action("ActionDealTile", &deal).unwrap();
        assert_eq!(events[0], Event::ReachAccepted { actor: 1 });
        assert_eq!(
            events[1],
            Event::Tsumo {
                actor: 2,
                pai: "?".to_string()
            }
        );
    }

    #[test]
    fn round_end_clears_operation_window() {
        let mut bridge = Bridge::new(0);
        let end = encode(pb::ActionNoTile::default());
        let events = bridge.handle_action("ActionNoTile", &end).unwrap();
        assert_eq!(events, vec![Event::EndKyoku]);
        assert_eq!(bridge.round_end_counter(), 1);
        assert!(bridge.last_operation_list().is_empty());
        assert!(bridge.last_operation_context().is_none());
    }

    #[test]
    fn bridge_tracks_own_tehai_and_tsumo_like_python_bridge() {
        let mut bridge = Bridge::new(0);
        let data = encode(pb::ActionNewRound {
            chang: 0,
            ju: 0,
            tiles: vec![
                "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1z", "2z", "3z", "4z",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
            doras: vec!["4p".to_string()],
            scores: vec![25000, 25000, 25000, 25000],
            ..Default::default()
        });
        bridge.handle_action("ActionNewRound", &data).unwrap();
        assert_eq!(bridge.my_tehais().len(), 13);
        assert_eq!(bridge.my_tsumohai(), None);

        let deal = encode(pb::ActionDealTile {
            seat: 0,
            tile: "0p".to_string(),
            ..Default::default()
        });
        bridge.handle_action("ActionDealTile", &deal).unwrap();
        assert_eq!(bridge.my_tsumohai(), Some("5pr"));

        let discard = encode(pb::ActionDiscardTile {
            seat: 0,
            tile: "2m".to_string(),
            moqie: false,
            ..Default::default()
        });
        bridge.handle_action("ActionDiscardTile", &discard).unwrap();
        assert_eq!(bridge.my_tsumohai(), None);
        assert!(!bridge.my_tehais().contains(&"2m".to_string()));
        assert!(bridge.my_tehais().contains(&"5pr".to_string()));

        let deal = encode(pb::ActionDealTile {
            seat: 0,
            tile: "9p".to_string(),
            ..Default::default()
        });
        bridge.handle_action("ActionDealTile", &deal).unwrap();
        let discard = encode(pb::ActionDiscardTile {
            seat: 0,
            tile: "9p".to_string(),
            moqie: true,
            ..Default::default()
        });
        bridge.handle_action("ActionDiscardTile", &discard).unwrap();
        assert_eq!(bridge.my_tsumohai(), None);
        assert!(!bridge.my_tehais().contains(&"9p".to_string()));
    }

    #[test]
    fn chi_peng_gang_maps_target_pai_and_consumed_tiles() {
        let mut bridge = Bridge::new(0);
        let chi = encode(pb::ActionChiPengGang {
            seat: 1,
            r#type: 0,
            tiles: vec!["2m".to_string(), "3m".to_string(), "4m".to_string()],
            froms: vec![1, 0, 1],
            operation: Some(pb::OptionalOperationList {
                seat: 1,
                operation_list: vec![pb::OptionalOperation {
                    r#type: 1,
                    ..Default::default()
                }],
                ..Default::default()
            }),
            ..Default::default()
        });
        let events = bridge.handle_action("ActionChiPengGang", &chi).unwrap();
        assert_eq!(
            events,
            vec![Event::Chi {
                actor: 1,
                target: 0,
                pai: "3m".to_string(),
                consumed: vec!["2m".to_string(), "4m".to_string()]
            }]
        );
        assert_eq!(
            bridge.last_operation_context().unwrap().source,
            "ActionChiPengGang"
        );

        let pon = encode(pb::ActionChiPengGang {
            seat: 2,
            r#type: 1,
            tiles: vec!["5p".to_string(), "0p".to_string(), "5p".to_string()],
            froms: vec![2, 3, 2],
            ..Default::default()
        });
        let events = bridge.handle_action("ActionChiPengGang", &pon).unwrap();
        assert_eq!(
            events,
            vec![Event::Pon {
                actor: 2,
                target: 3,
                pai: "5pr".to_string(),
                consumed: vec!["5p".to_string(), "5p".to_string()]
            }]
        );

        let daiminkan = encode(pb::ActionChiPengGang {
            seat: 3,
            r#type: 2,
            tiles: vec!["7z".to_string(), "7z".to_string(), "7z".to_string(), "7z".to_string()],
            froms: vec![3, 3, 1, 3],
            ..Default::default()
        });
        let events = bridge
            .handle_action("ActionChiPengGang", &daiminkan)
            .unwrap();
        assert_eq!(
            events,
            vec![Event::Daiminkan {
                actor: 3,
                target: 1,
                pai: "C".to_string(),
                consumed: vec!["C".to_string(), "C".to_string(), "C".to_string()]
            }]
        );
    }

    #[test]
    fn ankan_and_kakan_expand_consumed_tiles_like_python_bridge() {
        let mut bridge = Bridge::new(0);
        let ankan = encode(pb::ActionAnGangAddGang {
            seat: 0,
            r#type: 3,
            tiles: "0m".to_string(),
            ..Default::default()
        });
        let events = bridge.handle_action("ActionAnGangAddGang", &ankan).unwrap();
        assert_eq!(
            events,
            vec![Event::Ankan {
                actor: 0,
                consumed: vec![
                    "5mr".to_string(),
                    "5m".to_string(),
                    "5m".to_string(),
                    "5m".to_string()
                ]
            }]
        );

        let kakan = encode(pb::ActionAnGangAddGang {
            seat: 0,
            r#type: 2,
            tiles: "5p".to_string(),
            doras: vec!["1s".to_string(), "9s".to_string()],
            ..Default::default()
        });
        let events = bridge.handle_action("ActionAnGangAddGang", &kakan).unwrap();
        assert_eq!(
            events,
            vec![Event::Kakan {
                actor: 0,
                pai: "5p".to_string(),
                consumed: vec!["5pr".to_string(), "5p".to_string(), "5p".to_string()]
            }]
        );
    }
}
