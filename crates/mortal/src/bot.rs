#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MjaiAction {
    Reach {
        actor: u32,
    },
    Dahai {
        actor: u32,
        pai: String,
        tsumogiri: bool,
    },
    None {
        can_act: bool,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MjaiEvent {
    Reach { actor: u32 },
}

pub trait Bot {
    fn react(&mut self, events: &[MjaiEvent]) -> Option<MjaiAction>;
}

pub fn resolve_riichi_discard<B: Bot>(
    initial: Option<MjaiAction>,
    bot: &mut B,
    player_id: u32,
    last_tsumo_tile: Option<&str>,
) -> Option<MjaiAction> {
    match initial {
        Some(MjaiAction::Reach { actor }) => {
            let response = bot.react(&[MjaiEvent::Reach { actor: player_id }]);
            match response {
                Some(MjaiAction::Dahai { .. }) => response,
                _ => last_tsumo_tile.map(|pai| MjaiAction::Dahai {
                    actor,
                    pai: pai.to_string(),
                    tsumogiri: true,
                }),
            }
        }
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct ScriptBot {
        response: Option<MjaiAction>,
        seen_events: Vec<MjaiEvent>,
    }

    impl Bot for ScriptBot {
        fn react(&mut self, events: &[MjaiEvent]) -> Option<MjaiAction> {
            self.seen_events.extend_from_slice(events);
            self.response.clone()
        }
    }

    #[test]
    fn riichi_resolution_feeds_reach_event_then_uses_model_dahai() {
        let mut bot = ScriptBot {
            response: Some(MjaiAction::Dahai {
                actor: 0,
                pai: "3p".to_string(),
                tsumogiri: false,
            }),
            seen_events: Vec::new(),
        };

        let resolved = resolve_riichi_discard(
            Some(MjaiAction::Reach { actor: 0 }),
            &mut bot,
            0,
            Some("9s"),
        );
        assert_eq!(
            resolved,
            Some(MjaiAction::Dahai {
                actor: 0,
                pai: "3p".to_string(),
                tsumogiri: false,
            })
        );
        assert_eq!(bot.seen_events, vec![MjaiEvent::Reach { actor: 0 }]);
    }

    #[test]
    fn riichi_resolution_falls_back_to_last_tsumo_when_model_does_not_discard() {
        let mut bot = ScriptBot {
            response: Some(MjaiAction::None { can_act: false }),
            seen_events: Vec::new(),
        };
        let resolved = resolve_riichi_discard(
            Some(MjaiAction::Reach { actor: 0 }),
            &mut bot,
            0,
            Some("9s"),
        );
        assert_eq!(
            resolved,
            Some(MjaiAction::Dahai {
                actor: 0,
                pai: "9s".to_string(),
                tsumogiri: true,
            })
        );
    }
}
