#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BotAction {
    Dahai { tile: String, tsumogiri: bool },
    None,
    Reach { tile: String, tsumogiri: bool },
    Hora { tsumo: bool },
    Ryukyoku,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationContext {
    pub source: String,
    pub seat: u32,
    pub received_key: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingAction {
    pub action: BotAction,
    pub context: Option<OperationContext>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Operation {
    pub r#type: u32,
    pub combination: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionState {
    pub current_context: Option<OperationContext>,
    pub operations: Vec<Operation>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RpcPlan {
    InputOperation {
        r#type: u32,
        tile: Option<String>,
        moqie: bool,
        timeuse: u32,
    },
    Skip,
    IgnoreStale,
    RefuseNoDiscardWindow,
}

pub const OP_DISCARD: u32 = 1;
pub const OP_LIQI: u32 = 7;
pub const OP_ZIMO: u32 = 8;
pub const OP_HU: u32 = 9;
pub const OP_LIU_JU: u32 = 10;

pub fn plan_action(pending: &PendingAction, state: &ActionState) -> RpcPlan {
    if let Some(expected) = &pending.context {
        if state.current_context.as_ref() != Some(expected) {
            return RpcPlan::IgnoreStale;
        }
    }

    match &pending.action {
        BotAction::Dahai { tile, tsumogiri } => {
            if !state.operations.iter().any(|op| op.r#type == OP_DISCARD) {
                return RpcPlan::RefuseNoDiscardWindow;
            }
            RpcPlan::InputOperation {
                r#type: OP_DISCARD,
                tile: Some(tile.clone()),
                moqie: *tsumogiri,
                timeuse: 3,
            }
        }
        BotAction::None => RpcPlan::Skip,
        BotAction::Reach { tile, tsumogiri } => {
            let valid_tiles = state
                .operations
                .iter()
                .find(|op| op.r#type == OP_LIQI)
                .map(|op| op.combination.as_slice())
                .unwrap_or(&[]);
            let (tile, _) = select_riichi_declaration_tile(tile, valid_tiles);
            RpcPlan::InputOperation {
                r#type: OP_LIQI,
                tile: Some(tile),
                moqie: *tsumogiri,
                timeuse: 3,
            }
        }
        BotAction::Hora { tsumo } => RpcPlan::InputOperation {
            r#type: if *tsumo { OP_ZIMO } else { OP_HU },
            tile: None,
            moqie: false,
            timeuse: 1,
        },
        BotAction::Ryukyoku => RpcPlan::InputOperation {
            r#type: OP_LIU_JU,
            tile: None,
            moqie: false,
            timeuse: 1,
        },
    }
}

pub fn select_riichi_declaration_tile(
    model_tile: &str,
    valid_tiles: &[String],
) -> (String, &'static str) {
    if valid_tiles.is_empty() {
        return (model_tile.to_string(), "model-no-candidates");
    }
    if valid_tiles.iter().any(|tile| tile == model_tile) {
        return (model_tile.to_string(), "model-exact");
    }

    let model_kind = candidate_kind(model_tile);
    for tile in valid_tiles {
        if candidate_kind(tile) == model_kind {
            return (tile.clone(), "model-red-equivalent");
        }
    }

    (valid_tiles[0].clone(), "fallback-first-candidate")
}

fn candidate_kind(tile: &str) -> String {
    if tile.len() == 2
        && tile.starts_with('0')
        && matches!(tile.chars().nth(1), Some('m' | 'p' | 's'))
    {
        format!("5{}", tile.chars().nth(1).unwrap())
    } else {
        tile.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx(key: u64) -> OperationContext {
        OperationContext {
            source: "ActionDealTile".to_string(),
            seat: 0,
            received_key: key,
        }
    }

    #[test]
    fn stale_operation_window_is_ignored_before_rpc() {
        let plan = plan_action(
            &PendingAction {
                action: BotAction::Dahai {
                    tile: "1m".to_string(),
                    tsumogiri: false,
                },
                context: Some(ctx(1)),
            },
            &ActionState {
                current_context: Some(ctx(2)),
                operations: vec![Operation {
                    r#type: OP_DISCARD,
                    combination: vec![],
                }],
            },
        );
        assert_eq!(plan, RpcPlan::IgnoreStale);
    }

    #[test]
    fn discard_without_discard_operation_window_is_refused() {
        let plan = plan_action(
            &PendingAction {
                action: BotAction::Dahai {
                    tile: "1m".to_string(),
                    tsumogiri: false,
                },
                context: Some(ctx(1)),
            },
            &ActionState {
                current_context: Some(ctx(1)),
                operations: vec![],
            },
        );
        assert_eq!(plan, RpcPlan::RefuseNoDiscardWindow);
    }

    #[test]
    fn riichi_uses_model_tile_when_candidate_matches() {
        let valid = vec!["1m".to_string(), "3p".to_string()];
        assert_eq!(
            select_riichi_declaration_tile("3p", &valid),
            ("3p".to_string(), "model-exact")
        );
    }

    #[test]
    fn riichi_uses_red_equivalent_candidate() {
        let valid = vec!["0m".to_string(), "3p".to_string()];
        assert_eq!(
            select_riichi_declaration_tile("5m", &valid),
            ("0m".to_string(), "model-red-equivalent")
        );
    }

    #[test]
    fn riichi_falls_back_to_first_candidate_only_when_model_tile_is_invalid() {
        let valid = vec!["1m".to_string(), "3p".to_string()];
        assert_eq!(
            select_riichi_declaration_tile("9s", &valid),
            ("1m".to_string(), "fallback-first-candidate")
        );
    }

    #[test]
    fn reach_action_plans_liqi_operation_with_selected_tile() {
        let plan = plan_action(
            &PendingAction {
                action: BotAction::Reach {
                    tile: "5m".to_string(),
                    tsumogiri: true,
                },
                context: Some(ctx(1)),
            },
            &ActionState {
                current_context: Some(ctx(1)),
                operations: vec![Operation {
                    r#type: OP_LIQI,
                    combination: vec!["0m".to_string(), "3p".to_string()],
                }],
            },
        );
        assert_eq!(
            plan,
            RpcPlan::InputOperation {
                r#type: OP_LIQI,
                tile: Some("0m".to_string()),
                moqie: true,
                timeuse: 3
            }
        );
    }
}
