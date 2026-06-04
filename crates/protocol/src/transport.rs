use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use http::header::{ACCEPT_LANGUAGE, ORIGIN, REFERER, USER_AGENT};
use liqi::codec::{pack_raw_request, pack_request, response_body};
use prost::Message;
use tokio::net::TcpStream;
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, Message as WsMessage},
    MaybeTlsStream, WebSocketStream,
};

const UA: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

type Ws = WebSocketStream<MaybeTlsStream<TcpStream>>;

pub struct LiqiSocket {
    ws: Ws,
    next_msg_id: u16,
}

impl LiqiSocket {
    pub async fn connect(url: &str) -> Result<Self> {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let mut request = url
            .into_client_request()
            .with_context(|| format!("invalid websocket URL {url}"))?;
        let headers = request.headers_mut();
        headers.insert(ORIGIN, "https://game.maj-soul.com".parse()?);
        headers.insert(REFERER, "https://game.maj-soul.com/1/".parse()?);
        headers.insert(USER_AGENT, UA.parse()?);
        headers.insert(ACCEPT_LANGUAGE, "zh-TW,zh;q=0.9,en;q=0.8".parse()?);

        let (ws, _) = connect_async(request)
            .await
            .with_context(|| format!("websocket connect failed: {url}"))?;
        Ok(Self { ws, next_msg_id: 1 })
    }

    pub async fn request_raw(&mut self, method: &str, body: &[u8]) -> Result<Vec<u8>> {
        let msg_id = self.alloc_msg_id();
        let packet = pack_raw_request(msg_id, method, body);
        self.ws.send(WsMessage::Binary(packet.into())).await?;
        self.read_response(msg_id).await
    }

    pub async fn request<Req, Res>(&mut self, method: &str, request: &Req) -> Result<Res>
    where
        Req: Message,
        Res: Message + Default,
    {
        let msg_id = self.alloc_msg_id();
        let packet = pack_request(msg_id, method, request);
        self.ws.send(WsMessage::Binary(packet.into())).await?;
        let raw = self.read_response(msg_id).await?;
        let (_, body) = response_body(&raw).map_err(|err| anyhow!(err))?;
        Res::decode(body.as_slice()).with_context(|| format!("decode response for {method}"))
    }

    pub async fn next_binary_frame(&mut self) -> Result<Vec<u8>> {
        self.read_binary_frame().await
    }

    async fn read_response(&mut self, expected_msg_id: u16) -> Result<Vec<u8>> {
        loop {
            let bytes = self.read_binary_frame().await?;
            if bytes.len() >= 3 && bytes[0] == 0x03 {
                let msg_id = u16::from_le_bytes([bytes[1], bytes[2]]);
                if msg_id == expected_msg_id {
                    return Ok(bytes);
                }
            }
        }
    }

    async fn read_binary_frame(&mut self) -> Result<Vec<u8>> {
        while let Some(message) = self.ws.next().await {
            let message = message?;
            let bytes = match message {
                WsMessage::Binary(bytes) => bytes.to_vec(),
                WsMessage::Text(text) => text.as_str().as_bytes().to_vec(),
                WsMessage::Ping(payload) => {
                    self.ws.send(WsMessage::Pong(payload)).await?;
                    continue;
                }
                WsMessage::Pong(_) => continue,
                WsMessage::Close(frame) => return Err(anyhow!("websocket closed: {frame:?}")),
                _ => continue,
            };
            return Ok(bytes);
        }
        Err(anyhow!("websocket ended before next frame"))
    }

    fn alloc_msg_id(&mut self) -> u16 {
        let msg_id = self.next_msg_id;
        self.next_msg_id = if self.next_msg_id == u16::MAX {
            1
        } else {
            self.next_msg_id + 1
        };
        msg_id
    }
}
