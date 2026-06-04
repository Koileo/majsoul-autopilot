#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtoBlock {
    Varint { id: u32, value: u64 },
    Bytes { id: u32, data: Vec<u8> },
}

const XOR_KEYS: [u8; 9] = [0x84, 0x5e, 0x4e, 0x42, 0x39, 0xa2, 0x1f, 0x60, 0x1c];

pub fn encode_action_payload(data: &[u8]) -> Vec<u8> {
    xor_action_payload(data)
}

pub fn decode_action_payload(data: &[u8]) -> Vec<u8> {
    xor_action_payload(data)
}

fn xor_action_payload(data: &[u8]) -> Vec<u8> {
    let len = data.len();
    data.iter()
        .enumerate()
        .map(|(i, byte)| {
            let mask = (((23 ^ len) + 5 * i + XOR_KEYS[i % XOR_KEYS.len()] as usize) & 0xff) as u8;
            byte ^ mask
        })
        .collect()
}

pub fn encode_varint(mut value: u64) -> Vec<u8> {
    if value == 0 {
        return vec![0];
    }

    let mut out = Vec::new();
    while value > 0 {
        let mut byte = (value & 0x7f) as u8;
        value >>= 7;
        if value > 0 {
            byte |= 0x80;
        }
        out.push(byte);
    }
    out
}

pub fn encode_blocks(blocks: &[ProtoBlock]) -> Vec<u8> {
    let mut out = Vec::new();
    for block in blocks {
        match block {
            ProtoBlock::Varint { id, value } => {
                out.extend(encode_varint(u64::from(*id) << 3));
                out.extend(encode_varint(*value));
            }
            ProtoBlock::Bytes { id, data } => {
                out.extend(encode_varint((u64::from(*id) << 3) | 2));
                out.extend(encode_varint(data.len() as u64));
                out.extend(data);
            }
        }
    }
    out
}

pub fn decode_blocks(buf: &[u8]) -> Result<Vec<ProtoBlock>, String> {
    let mut pos = 0;
    let mut blocks = Vec::new();

    while pos < buf.len() {
        let (tag, next) = decode_varint_at(buf, pos)?;
        pos = next;
        let id = (tag >> 3) as u32;
        let wire_type = tag & 0x07;
        match wire_type {
            0 => {
                let (value, next) = decode_varint_at(buf, pos)?;
                pos = next;
                blocks.push(ProtoBlock::Varint { id, value });
            }
            2 => {
                let (len, next) = decode_varint_at(buf, pos)?;
                pos = next;
                let len = len as usize;
                let end = pos
                    .checked_add(len)
                    .ok_or_else(|| "protobuf block length overflow".to_string())?;
                if end > buf.len() {
                    return Err("protobuf bytes block extends past buffer".to_string());
                }
                blocks.push(ProtoBlock::Bytes {
                    id,
                    data: buf[pos..end].to_vec(),
                });
                pos = end;
            }
            other => {
                return Err(format!("unsupported protobuf wire type {other}"));
            }
        }
    }

    Ok(blocks)
}

fn decode_varint_at(buf: &[u8], mut pos: usize) -> Result<(u64, usize), String> {
    let mut value = 0u64;
    let mut shift = 0u32;
    while pos < buf.len() {
        let byte = buf[pos];
        pos += 1;
        value |= u64::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            return Ok((value, pos));
        }
        shift += 7;
        if shift >= 64 {
            return Err("varint too long".to_string());
        }
    }
    Err("truncated varint".to_string())
}

pub fn pack_raw_request(msg_id: u16, method: &str, body: &[u8]) -> Vec<u8> {
    let mut out = vec![0x02];
    out.extend(msg_id.to_le_bytes());
    out.extend(encode_blocks(&[
        ProtoBlock::Bytes {
            id: 1,
            data: method.as_bytes().to_vec(),
        },
        ProtoBlock::Bytes {
            id: 2,
            data: body.to_vec(),
        },
    ]));
    out
}

pub fn pack_request<M: prost::Message>(msg_id: u16, method: &str, message: &M) -> Vec<u8> {
    let mut body = Vec::new();
    message
        .encode(&mut body)
        .expect("encoding prost message into Vec cannot fail");
    pack_raw_request(msg_id, method, &body)
}

pub fn response_body(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    if raw.len() < 3 {
        return Err("response frame too short".to_string());
    }
    if raw[0] != 0x03 {
        return Err(format!("expected response frame type 3, got {}", raw[0]));
    }
    let msg_id = u16::from_le_bytes([raw[1], raw[2]]);
    let blocks = decode_blocks(&raw[3..])?;
    let body = blocks
        .into_iter()
        .find_map(|block| match block {
            ProtoBlock::Bytes { id: 2, data } => Some(data),
            _ => None,
        })
        .ok_or_else(|| "response body block id=2 missing".to_string())?;
    Ok((msg_id, body))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xor_action_payload_matches_python_golden_and_round_trips() {
        let payload = b"ActionNewRound";
        let encoded = encode_action_payload(payload);
        assert_eq!(hex::encode(&encoded), "dc1f050309ba18f92a98c6ebf9f7");
        assert_eq!(decode_action_payload(&encoded), payload);
    }

    #[test]
    fn varint_encoding_matches_python_golden() {
        let cases = [
            (0, "00"),
            (1, "01"),
            (127, "7f"),
            (128, "8001"),
            (300, "ac02"),
            (16_384, "808001"),
        ];
        for (value, expected_hex) in cases {
            assert_eq!(hex::encode(encode_varint(value)), expected_hex);
        }
    }

    #[test]
    fn protobuf_block_encoding_matches_python_golden() {
        let body = vec![
            ProtoBlock::Varint { id: 2, value: 2 },
            ProtoBlock::Bytes {
                id: 3,
                data: b"route-2".to_vec(),
            },
        ];
        let route_body = encode_blocks(&body);
        let blocks = vec![
            ProtoBlock::Bytes {
                id: 1,
                data: b".lq.Route.requestConnection".to_vec(),
            },
            ProtoBlock::Bytes {
                id: 2,
                data: route_body,
            },
        ];
        let encoded = encode_blocks(&blocks);
        assert_eq!(
            hex::encode(&encoded),
            "0a1b2e6c712e526f7574652e72657175657374436f6e6e656374696f6e120b10021a07726f7574652d32"
        );
        assert_eq!(decode_blocks(&encoded).unwrap(), blocks);
    }

    #[test]
    fn raw_request_packing_matches_python_golden() {
        let body = encode_blocks(&[
            ProtoBlock::Varint { id: 2, value: 2 },
            ProtoBlock::Bytes {
                id: 3,
                data: b"route-2".to_vec(),
            },
        ]);
        let raw = pack_raw_request(7, ".lq.Route.requestConnection", &body);
        assert_eq!(
            hex::encode(raw),
            "0207000a1b2e6c712e526f7574652e72657175657374436f6e6e656374696f6e120b10021a07726f7574652d32"
        );
    }

    #[test]
    fn response_body_extracts_msg_id_and_payload() {
        let raw = [
            vec![0x03, 0x2a, 0x00],
            encode_blocks(&[
                ProtoBlock::Bytes {
                    id: 1,
                    data: Vec::new(),
                },
                ProtoBlock::Bytes {
                    id: 2,
                    data: b"payload".to_vec(),
                },
            ]),
        ]
        .concat();
        let (msg_id, body) = response_body(&raw).unwrap();
        assert_eq!(msg_id, 42);
        assert_eq!(body, b"payload");
    }
}
