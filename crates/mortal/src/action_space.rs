pub const ACTION_LABELS: [&str; 46] = [
    "1m",
    "2m",
    "3m",
    "4m",
    "5m",
    "6m",
    "7m",
    "8m",
    "9m",
    "1p",
    "2p",
    "3p",
    "4p",
    "5p",
    "6p",
    "7p",
    "8p",
    "9p",
    "1s",
    "2s",
    "3s",
    "4s",
    "5s",
    "6s",
    "7s",
    "8s",
    "9s",
    "E",
    "S",
    "W",
    "N",
    "P",
    "F",
    "C",
    "5mr",
    "5pr",
    "5sr",
    "reach",
    "chi_low",
    "chi_mid",
    "chi_high",
    "pon",
    "kan_select",
    "hora",
    "ryukyoku",
    "none",
];

pub fn label_for_index(index: usize) -> Option<&'static str> {
    ACTION_LABELS.get(index).copied()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn action_space_matches_python_order_used_for_logging() {
        assert_eq!(ACTION_LABELS.len(), 46);
        assert_eq!(label_for_index(0), Some("1m"));
        assert_eq!(label_for_index(33), Some("C"));
        assert_eq!(label_for_index(34), Some("5mr"));
        assert_eq!(label_for_index(37), Some("reach"));
        assert_eq!(label_for_index(45), Some("none"));
        assert_eq!(label_for_index(46), None);
    }
}
