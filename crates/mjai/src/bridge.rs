use anyhow::Result;
use liqi::pb;
use prost::Message;

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
    EndKyoku,
    EndGame,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationContext {
    pub source: String,
    pub seat: u32,
    pub time_add: u32,
    pub time_fixed: u32,
    pub passed_waiting_time: u32,
}

#[derive(Debug, Default)]
pub struct Bridge {
    seat: u32,
    doras: Vec<String>,
    accept_reach: Option<Event>,
    last_operation_list: Vec<pb::OptionalOperation>,
    last_operation_context: Option<OperationContext>,
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

    pub fn handle_action(&mut self, name: &str, data: &[u8]) -> Result<Vec<Event>> {
        let mut events = Vec::new();
        if let Some(event) = self.accept_reach.take() {
            events.push(event);
        }

        match name {
            "ActionNewRound" => {
                let action = pb::ActionNewRound::decode(data)?;
                self.all_ready_new_round(&action, &mut events);
            }
            "ActionDealTile" => {
                let action = pb::ActionDealTile::decode(data)?;
                if let Some(operation) = action.operation {
                    self.set_operation("ActionDealTile", operation, 0);
                } else {
                    self.clear_operation();
                }
                let pai = if action.tile.is_empty() {
                    "?".to_string()
                } else {
                    ms_tile_to_mjai(&action.tile)
                };
                events.push(Event::Tsumo {
                    actor: action.seat,
                    pai,
                });
                self.update_dora_events(&action.doras);
            }
            "ActionDiscardTile" => {
                let action = pb::ActionDiscardTile::decode(data)?;
                if let Some(operation) = action.operation {
                    self.set_operation("ActionDiscardTile", operation, 0);
                } else {
                    self.clear_operation();
                }
                self.discard_counter += 1;
                if action.is_liqi {
                    events.push(Event::Reach { actor: action.seat });
                }
                events.push(Event::Dahai {
                    actor: action.seat,
                    pai: ms_tile_to_mjai(&action.tile),
                    tsumogiri: action.moqie,
                });
                if action.is_liqi {
                    self.accept_reach = Some(Event::ReachAccepted { actor: action.seat });
                }
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

    fn all_ready_new_round(&mut self, action: &pb::ActionNewRound, events: &mut Vec<Event>) {
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
            events.push(Event::Tsumo {
                actor: self.seat,
                pai: ms_tile_to_mjai(&action.tiles[13]),
            });
        }

        if let Some(operation) = action.operation.clone() {
            self.set_operation("ActionNewRound", operation, 0);
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
}
