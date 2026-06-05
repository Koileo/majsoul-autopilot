#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Room {
    Bronze,
    Silver,
    Gold,
    Jade,
    Throne,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    FourPlayerEast,
    FourPlayerSouth,
}

pub fn rank_tier_from_level_id(level_id: u32) -> u32 {
    if level_id >= 10_000 {
        (level_id / 100) % 100
    } else {
        level_id / 100
    }
}

pub fn target_mode_for_rank_level(level_id: u32) -> (Mode, Room) {
    match rank_tier_from_level_id(level_id) {
        tier if tier >= 5 => (Mode::FourPlayerSouth, Room::Throne),
        tier if tier >= 4 => (Mode::FourPlayerSouth, Room::Jade),
        tier if tier >= 3 => (Mode::FourPlayerSouth, Room::Gold),
        tier if tier >= 2 => (Mode::FourPlayerSouth, Room::Silver),
        _ => (Mode::FourPlayerEast, Room::Bronze),
    }
}

pub fn match_sid(mode: &Mode, room: &Room) -> Option<String> {
    let id = match (mode, room) {
        (Mode::FourPlayerEast, Room::Bronze) => 2,
        (Mode::FourPlayerSouth, Room::Bronze) => 3,
        (Mode::FourPlayerEast, Room::Silver) => 5,
        (Mode::FourPlayerSouth, Room::Silver) => 6,
        (Mode::FourPlayerEast, Room::Gold) => 8,
        (Mode::FourPlayerSouth, Room::Gold) => 9,
        (Mode::FourPlayerEast, Room::Jade) => 11,
        (Mode::FourPlayerSouth, Room::Jade) => 12,
        (Mode::FourPlayerEast, Room::Throne) => 15,
        (Mode::FourPlayerSouth, Room::Throne) => 16,
    };
    Some(format!("1:{id}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rank_target_matches_python_policy() {
        assert_eq!(rank_tier_from_level_id(10101), 1);
        assert_eq!(rank_tier_from_level_id(10201), 2);
        assert_eq!(rank_tier_from_level_id(10302), 3);
        assert_eq!(rank_tier_from_level_id(10403), 4);
        assert_eq!(rank_tier_from_level_id(10501), 5);

        assert_eq!(
            target_mode_for_rank_level(10101),
            (Mode::FourPlayerEast, Room::Bronze)
        );
        assert_eq!(
            target_mode_for_rank_level(10201),
            (Mode::FourPlayerSouth, Room::Silver)
        );
        assert_eq!(
            target_mode_for_rank_level(10302),
            (Mode::FourPlayerSouth, Room::Gold)
        );
        assert_eq!(
            target_mode_for_rank_level(10403),
            (Mode::FourPlayerSouth, Room::Jade)
        );
        assert_eq!(
            target_mode_for_rank_level(10501),
            (Mode::FourPlayerSouth, Room::Throne)
        );
    }

    #[test]
    fn match_sid_matches_cfg_desktop_matchmode_ids() {
        assert_eq!(
            match_sid(&Mode::FourPlayerEast, &Room::Bronze),
            Some("1:2".to_string())
        );
        assert_eq!(
            match_sid(&Mode::FourPlayerSouth, &Room::Silver),
            Some("1:6".to_string())
        );
        assert_eq!(
            match_sid(&Mode::FourPlayerSouth, &Room::Gold),
            Some("1:9".to_string())
        );
        assert_eq!(
            match_sid(&Mode::FourPlayerSouth, &Room::Jade),
            Some("1:12".to_string())
        );
        assert_eq!(
            match_sid(&Mode::FourPlayerSouth, &Room::Throne),
            Some("1:16".to_string())
        );
    }
}
