use liqi::codec::{encode_blocks, ProtoBlock};

pub const DEFAULT_ROUTE_ID: &str = "route-2";
pub const LOBBY_ROUTE_IDS: [&str; 5] = ["route-2", "route-3", "route-4", "route-5", "route-6"];
pub const GAME_ROUTE_IDS: [&str; 5] = ["route-6", "route-5", "route-4", "route-3", "route-2"];

pub fn route_ws_url(route_id: &str, tail: &str) -> String {
    let port = if route_id == "route-3" { 8443 } else { 443 };
    format!(
        "wss://{route_id}.maj-soul.com:{port}/{}",
        tail.trim_matches('/')
    )
}

pub fn lobby_ws_url_candidates() -> Vec<String> {
    LOBBY_ROUTE_IDS
        .iter()
        .map(|route_id| route_ws_url(route_id, "gateway"))
        .collect()
}

pub fn route_body(kind: u64, route_id: &str, timestamp_ms: u64) -> Vec<u8> {
    encode_blocks(&[
        ProtoBlock::Varint { id: 2, value: kind },
        ProtoBlock::Bytes {
            id: 3,
            data: route_id.as_bytes().to_vec(),
        },
        ProtoBlock::Varint {
            id: 4,
            value: timestamp_ms,
        },
    ])
}

pub fn prepare_login_body(access_token: &str) -> Vec<u8> {
    encode_blocks(&[
        ProtoBlock::Bytes {
            id: 1,
            data: access_token.as_bytes().to_vec(),
        },
        ProtoBlock::Varint { id: 2, value: 0 },
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_three_uses_8443_and_other_routes_use_443() {
        assert_eq!(
            route_ws_url("route-3", "gateway"),
            "wss://route-3.maj-soul.com:8443/gateway"
        );
        assert_eq!(
            route_ws_url("route-2", "gateway"),
            "wss://route-2.maj-soul.com:443/gateway"
        );
        assert_eq!(
            route_ws_url("route-6", "game-gateway-zone"),
            "wss://route-6.maj-soul.com:443/game-gateway-zone"
        );
    }

    #[test]
    fn lobby_candidates_match_python_order() {
        assert_eq!(
            lobby_ws_url_candidates(),
            vec![
                "wss://route-2.maj-soul.com:443/gateway",
                "wss://route-3.maj-soul.com:8443/gateway",
                "wss://route-4.maj-soul.com:443/gateway",
                "wss://route-5.maj-soul.com:443/gateway",
                "wss://route-6.maj-soul.com:443/gateway",
            ]
        );
    }

    #[test]
    fn route_body_matches_python_shape() {
        assert_eq!(
            hex::encode(route_body(2, "route-2", 1_717_000_000_000)),
            "10021a07726f7574652d322080a4b3a9fc31"
        );
    }

    #[test]
    fn prepare_login_body_matches_python_shape() {
        assert_eq!(
            hex::encode(prepare_login_body("token")),
            "0a05746f6b656e1000"
        );
    }
}
