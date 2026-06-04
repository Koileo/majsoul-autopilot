pub mod codec;

pub mod pb {
    include!(concat!(env!("OUT_DIR"), "/lq.rs"));
}

#[cfg(test)]
mod proto_tests {
    use super::pb;

    #[test]
    fn generated_proto_exposes_core_runtime_messages() {
        let login = pb::ReqLogin {
            account: "user@example.com".to_string(),
            password: "digest".to_string(),
            reconnect: false,
            ..Default::default()
        };
        assert_eq!(login.account, "user@example.com");

        let op = pb::ReqSelfOperation {
            r#type: 1,
            tile: "1m".to_string(),
            timeuse: 3,
            ..Default::default()
        };
        assert_eq!(op.r#type, 1);
        assert_eq!(op.tile, "1m");
    }
}
